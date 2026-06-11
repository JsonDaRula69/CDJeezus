"""Self-update mechanism for CDJeezus.

Stops the daemon, upgrades the package via pip/uv, preserves config and
state, then restarts. Also handles data migrations between versions.

When any code change modifies the format of state.json, config, or
other operational data, a migration step must be added here.
"""

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .config import CONFIG_DIR, STATE_FILE
from .setup import kill_running_daemon, register_launchdaemon, INSTALLED_PLIST, ENV_FILE
from .daemon import request_stop

logger = logging.getLogger(__name__)

CURRENT_STATE_VERSION = 5  # Increment when state.json schema changes


def _get_installed_version() -> str:
    """Get the currently installed version."""
    from . import __version__
    return __version__


def _get_latest_version() -> str | None:
    """Check PyPI for the latest released version."""
    try:
        import urllib.request
        url = "https://pypi.org/pypi/cdjeezus/json"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data["info"]["version"]
    except Exception as e:
        logger.error("Could not check PyPI for updates: %s", e)
        return None


def _migrate_state(state: dict) -> dict:
    """Migrate state.json to the current schema.

    v1 (pre-0.20.0): No version field, downloaded entries lack label_name.
    v2 (0.20.0+): Has version field, downloaded entries may have label_name.
    v3 (0.24.0+): Adds serato_blocked_transfer flag.
    v4 (0.25.0+): Adds verification fields (verified, verification_method, verification_confidence).
    v5 (0.26.0+): Adds library_fingerprinted and upscale_prompted flags.
    """
    version = state.get("version", 1)

    if version < 2:
        # v1 -> v2: Add label_name field awareness
        for url, playlist in state.get("playlists", {}).items():
            for tid, info in playlist.get("downloaded", {}).items():
                info.setdefault("local_path", "")
                info.setdefault("downloaded_at", "")
        logger.info("Migrated state from v1 to v2")

    if version < 3:
        # v2 -> v3: Add serato_blocked_transfer flag
        state.setdefault("serato_blocked_transfer", False)
        logger.info("Migrated state from v2 to v3")

    if version < 4:
        # v3 -> v4: Add verification fields to downloaded entries
        for url, playlist in state.get("playlists", {}).items():
            for tid, info in playlist.get("downloaded", {}).items():
                info.setdefault("verified", None)
                info.setdefault("verification_method", "")
                info.setdefault("verification_confidence", 0.0)
        logger.info("Migrated state from v3 to v4")

    if version < 5:
        # v4 -> v5: Add library fingerprinting and upscale tracking
        state.setdefault("library_fingerprinted", False)
        state.setdefault("upscale_prompted", False)
        logger.info("Migrated state from v4 to v5")

    state["version"] = CURRENT_STATE_VERSION
    return state


def _migrate_env() -> None:
    """Migrate .env config file: add missing keys with defaults.

    When new config options are added, existing .env files won't have them.
    This adds any missing keys without overwriting user values.
    """
    if not ENV_FILE.exists():
        return

    # Default values for all config keys (used when key is missing from .env)
    defaults = {
        "PRIMARY_DJ": "serato",
        "TWO_WAY_SYNC": "0",
        "SOUNDCLOUD_POLL_INTERVAL": "300",
        "SEARCH_TIMEOUT": "30",
        "PREFER_FREE_SLOTS": "1",
        "MIN_FILESIZE_MB": "5",
        "SERATO_CHECK_INTERVAL": "30",
        "FINGERPRINT_VERIFY": "1",
        "UPSCALE_ENABLED": "0",
        "AUTO_UPDATE_INTERVAL": "14400",
    }

    try:
        lines = ENV_FILE.read_text().splitlines()
        existing_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=", 1)[0].strip()
                existing_keys.add(key)
            new_lines.append(line)

        # Add missing defaults at the end
        for key, default in defaults.items():
            if key not in existing_keys:
                new_lines.append(f"{key}={default}")

        ENV_FILE.write_text("\n".join(new_lines) + "\n")
        logger.info("Migrated .env config (added %d missing keys)",
                     len(set(defaults.keys()) - existing_keys))
    except Exception as e:
        logger.warning("Could not migrate .env: %s", e)


def _backup_config() -> Path:
    """Back up config directory before update."""
    backup_dir = CONFIG_DIR / "backups" / f"pre-update-{_get_installed_version()}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for f in (ENV_FILE, STATE_FILE):
        if f.exists():
            shutil.copy2(f, backup_dir / f.name)

    logger.info("Config backed up to %s", backup_dir)
    return backup_dir



def check_for_update() -> str | None:
    """Check PyPI for a newer version. Returns latest version string or None."""
    latest = _get_latest_version()
    if latest is None:
        return None
    current = _get_installed_version()
    if latest != current:
        logger.info("Update available: v%s → v%s", current, latest)
        return latest
    return None


def auto_update_if_available() -> bool:
    """Check for updates and signal the daemon to auto-update on next restart.

    Called periodically by the daemon (on startup and every 4 hours).
    Instead of upgrading in-process (which is fragile), this:
      1. Checks PyPI for a newer version
      2. If found, writes an update-pending flag with version info
      3. Sends SIGUSR1 to trigger a graceful shutdown
      4. The LaunchAgent relaunches the daemon, which detects the flag
         and performs the upgrade before starting the main loop.

    Returns True if an update was scheduled (daemon will exit).
    Returns False if no update was needed or if the check failed.
    """
    latest = check_for_update()
    if latest is None:
        return False

    current = _get_installed_version()
    logger.info("Auto-update available: v%s → v%s", current, latest)

    # Write update-pending flag so the next launch performs the upgrade
    update_flag = CONFIG_DIR / "auto-update-pending"
    try:
        update_flag.write_text(f"{current}\n{latest}\n")
        update_flag.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Auto-update scheduled: daemon will upgrade on next launch")
    except Exception as e:
        logger.error("Auto-update: could not write update flag: %s", e)
        return False

    # Notify the user
    from .notify import send_notification
    send_notification("CDJeezus Update", f"Updating to v{latest}...")

    return True


def perform_pending_update() -> bool:
    """Check for and perform a pending auto-update.

    Called at startup before the main daemon loop begins. If an
    auto-update-pending flag exists, this function:
      1. Backs up config and state
      2. Upgrades the package via uv/pip
      3. Migrates data (state.json and .env)
      4. Removes the flag
      5. Returns True so the caller can re-import and restart

    Returns True if an update was performed, False otherwise.
    """
    update_flag = CONFIG_DIR / "auto-update-pending"
    if not update_flag.exists():
        return False

    try:
        versions = update_flag.read_text().strip().split("\n")
        current = versions[0] if len(versions) > 0 else "unknown"
        target = versions[1] if len(versions) > 1 else "unknown"
    except Exception:
        current = "unknown"
        target = "unknown"

    logger.info("Performing pending auto-update: v%s → v%s", current, target)

    # Back up config and state
    try:
        _backup_config()
    except Exception as e:
        logger.error("Auto-update backup failed: %s", e)
        # Continue anyway — we have the flag, better to try the upgrade

    # Unload LaunchAgent during upgrade
    for plist in (INSTALLED_PLIST,):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)

    # Upgrade package
    try:
        result = subprocess.run(
            ["uv", "tool", "install", "cdjeezus", "--force", "--reinstall"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "cdjeezus"],
                capture_output=True, text=True, timeout=120,
            )
        if result.returncode != 0:
            logger.error("Auto-update upgrade failed: %s", result.stderr or result.stdout)
            update_flag.unlink(missing_ok=True)
            return False
    except subprocess.TimeoutExpired:
        logger.error("Auto-update upgrade timed out")
        update_flag.unlink(missing_ok=True)
        return False

    # Migrate data
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            state = _migrate_state(state)
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.warning("Auto-update state migration issue: %s", e)

    try:
        _migrate_env()
    except Exception as e:
        logger.warning("Auto-update config migration issue: %s", e)

    # Remove flag
    update_flag.unlink(missing_ok=True)

    # Re-register LaunchAgent
    try:
        register_launchdaemon()
    except Exception as e:
        logger.warning("Auto-update: could not re-register LaunchAgent: %s", e)

    logger.info("Auto-update complete: v%s → v%s", current, target)

    from .notify import send_notification
    send_notification("CDJeezus Updated", f"Updated to v{target}")

    return True


def run_update(check_only: bool = False) -> None:
    """Main update entry point.

    1. Check for latest version on PyPI
    2. If check_only, just report and return
    3. Back up config and state
    4. Stop daemon gracefully
    5. Upgrade the package
    6. Migrate data if needed
    7. Restart daemon
    """
    current = _get_installed_version()
    latest = _get_latest_version()

    if latest is None:
        print(f"  Could not check for updates. Current version: v{current}")
        print("  Make sure you have internet connectivity and try again.")
        sys.exit(1)

    print(f"  Current version: v{current}")
    print(f"  Latest version:   v{latest}")

    if current == latest:
        print(f"  Already up to date!")
        return

    if check_only:
        print(f"  Update available: v{current} → v{latest}")
        print(f"  Run 'cdjeezus update' to install.")
        return

    print(f"  Updating CDJeezus v{current} → v{latest}...")
    print()

    # Step 1: Back up config and state
    print("  [1/6] Backing up config...")
    backup_dir = _backup_config()
    print(f"  ✓ Config backed up to {backup_dir}")

    # Step 2: Stop daemon gracefully
    print("  [2/6] Stopping daemon...")
    stopped = request_stop(timeout=60)
    if stopped:
        print("  ✓ Daemon stopped gracefully")
    else:
        print("  ⚠ Daemon did not respond to graceful stop, force-killing...")
        kill_running_daemon()
        print("  ✓ Daemon force-stopped")

    # Step 3: Unload LaunchAgent
    print("  [3/6] Unloading LaunchAgent...")
    for plist in (INSTALLED_PLIST,):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
    print("  ✓ LaunchAgent unloaded")

    # Step 4: Upgrade package
    print("  [4/6] Upgrading package...")
    try:
        result = subprocess.run(
            ["uv", "tool", "install", "cdjeezus", "--force", "--reinstall"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "cdjeezus"],
                capture_output=True, text=True, timeout=120,
            )
        if result.returncode != 0:
            print(f"  ✗ Upgrade failed:\n{result.stderr or result.stdout}")
            print("  Restoring from backup...")
            for f in (ENV_FILE, STATE_FILE):
                backup = backup_dir / f.name
                if backup.exists():
                    shutil.copy2(backup, f)
            sys.exit(1)
        print("  ✓ Package upgraded")
    except subprocess.TimeoutExpired:
        print("  ✗ Upgrade timed out. Restoring from backup...")
        for f in (ENV_FILE, STATE_FILE):
            backup = backup_dir / f.name
            if backup.exists():
                shutil.copy2(backup, f)
        sys.exit(1)

    # Step 5: Migrate data
    print("  [5/6] Migrating data...")
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            state = _migrate_state(state)
            STATE_FILE.write_text(json.dumps(state, indent=2))
            print("  ✓ State migrated")
        except Exception as e:
            logger.warning("State migration failed: %s", e)
            backup_state = backup_dir / "state.json"
            if backup_state.exists():
                shutil.copy2(backup_state, STATE_FILE)
            print("  ⚠ State migration had issues, restored from backup")

    # Migrate .env config: add missing keys with defaults
    try:
        _migrate_env()
        print("  ✓ Config migrated")
    except Exception as e:
        logger.warning("Config migration failed: %s", e)
        print("  ⚠ Config migration had issues (manual review recommended)")

    # Step 6: Regenerate LaunchAgent plist and restart
    print("  [6/6] Restarting daemon...")
    # Always regenerate plist so it uses the current Python path
    try:
        register_launchdaemon()
        print("  ✓ LaunchAgent regenerated and loaded")
    except Exception as e:
        logger.warning("Could not regenerate LaunchAgent: %s", e)
        # Fall back to just reloading if it exists
        if INSTALLED_PLIST.exists():
            subprocess.run(["launchctl", "load", str(INSTALLED_PLIST)], capture_output=True, check=False)
            print("  ✓ LaunchAgent reloaded")
        else:
            print("  ⚠ Could not register LaunchAgent. Run 'cdjeezus setup' to fix.")

    new_version = _get_installed_version()
    print()
    print(f"  ───────────────────────────────────────────")
    print(f"  Update complete! v{current} → v{new_version}")
    print()
    if new_version != latest:
        print(f"  Note: installed v{new_version} differs from PyPI v{latest}")
        print(f"  You may need to run 'cdjeezus update' again.")
    print(f"  ───────────────────────────────────────────")
    print()
