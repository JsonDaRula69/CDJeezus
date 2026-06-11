"""Self-update mechanism for StreamFLACr.

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

CURRENT_STATE_VERSION = 4  # Increment when state.json schema changes


def _get_installed_version() -> str:
    """Get the currently installed version."""
    from . import __version__
    return __version__


def _get_latest_version() -> str | None:
    """Check PyPI for the latest released version."""
    try:
        import urllib.request
        url = "https://pypi.org/pypi/streamflacr/json"
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

    state["version"] = CURRENT_STATE_VERSION
    return state


def _backup_config() -> Path:
    """Back up config directory before update."""
    backup_dir = CONFIG_DIR / "backups" / f"pre-update-{_get_installed_version()}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for f in (ENV_FILE, STATE_FILE):
        if f.exists():
            shutil.copy2(f, backup_dir / f.name)

    logger.info("Config backed up to %s", backup_dir)
    return backup_dir


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
        print(f"  Run 'streamflacr update' to install.")
        return

    print(f"  Updating StreamFLACr v{current} → v{latest}...")
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
            ["uv", "tool", "install", "streamflacr", "--force", "--reinstall"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "streamflacr"],
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

    # Step 6: Reload LaunchAgent and restart
    print("  [6/6] Restarting daemon...")
    if INSTALLED_PLIST.exists():
        subprocess.run(["launchctl", "load", str(INSTALLED_PLIST)], capture_output=True, check=False)
        print("  ✓ LaunchAgent reloaded")
    else:
        print("  No LaunchAgent found. Run 'streamflacr setup' to register one.")

    new_version = _get_installed_version()
    print()
    print(f"  ───────────────────────────────────────────")
    print(f"  Update complete! v{current} → v{new_version}")
    print()
    if new_version != latest:
        print(f"  Note: installed v{new_version} differs from PyPI v{latest}")
        print(f"  You may need to run 'streamflacr update' again.")
    print(f"  ───────────────────────────────────────────")
    print()
