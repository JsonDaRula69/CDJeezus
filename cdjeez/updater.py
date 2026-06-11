"""Self-update mechanism for CDJeez.

Stops the daemon, upgrades the package via pip/uv, preserves config and
state, then restarts. Also handles data migrations between versions.
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
from .style import ok, warn, fail, dim, step, separator, boxed, summary_box, console

logger = logging.getLogger(__name__)

CURRENT_STATE_VERSION = 5


def _get_installed_version() -> str:
    from . import __version__
    return __version__


def _get_latest_version() -> str | None:
    try:
        import urllib.request
        url = "https://pypi.org/pypi/cdjeez/json"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data["info"]["version"]
    except Exception as e:
        logger.error("Could not check PyPI for updates: %s", e)
        return None


def _migrate_state(state: dict) -> dict:
    version = state.get("version", 1)
    if version < 2:
        for url, playlist in state.get("playlists", {}).items():
            for tid, info in playlist.get("downloaded", {}).items():
                info.setdefault("local_path", "")
                info.setdefault("downloaded_at", "")
    if version < 3:
        state.setdefault("serato_blocked_transfer", False)
    if version < 4:
        for url, playlist in state.get("playlists", {}).items():
            for tid, info in playlist.get("downloaded", {}).items():
                info.setdefault("verified", None)
                info.setdefault("verification_method", "")
                info.setdefault("verification_confidence", 0.0)
    if version < 5:
        state.setdefault("library_fingerprinted", False)
        state.setdefault("upscale_prompted", False)
    state["version"] = CURRENT_STATE_VERSION
    return state


def _migrate_env() -> None:
    if not ENV_FILE.exists():
        return
    defaults = {
        "PRIMARY_DJ": "serato", "TWO_WAY_SYNC": "0",
        "SOUNDCLOUD_POLL_INTERVAL": "300", "SEARCH_TIMEOUT": "30",
        "PREFER_FREE_SLOTS": "1", "MIN_FILESIZE_MB": "5",
        "SERATO_CHECK_INTERVAL": "30", "FINGERPRINT_VERIFY": "1",
        "UPSCALE_ENABLED": "0", "AUTO_UPDATE_INTERVAL": "14400",
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
        for key, default in defaults.items():
            if key not in existing_keys:
                new_lines.append(f"{key}={default}")
        ENV_FILE.write_text("\n".join(new_lines) + "\n")
    except Exception as e:
        logger.warning("Could not migrate .env: %s", e)


def _backup_config() -> Path:
    backup_dir = CONFIG_DIR / "backups" / f"pre-update-{_get_installed_version()}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in (ENV_FILE, STATE_FILE):
        if f.exists():
            shutil.copy2(f, backup_dir / f.name)
    return backup_dir


def auto_update_if_available() -> None:
    latest = _get_latest_version()
    if latest is None:
        return
    current = _get_installed_version()
    if latest != current:
        logger.info("New version available: v%s (current: v%s)", latest, current)
        flag = CONFIG_DIR / "auto-update-pending"
        flag.write_text(latest)


def perform_pending_update() -> bool:
    flag = CONFIG_DIR / "auto-update-pending"
    if not flag.exists():
        return False
    target_version = flag.read_text().strip()
    flag.unlink(missing_ok=True)

    step(0, 6, f'Auto-updating to v{target_version}...')
    backup_dir = _backup_config()
    ok("Config backed up")

    try:
        result = subprocess.run(
            ["uv", "tool", "install", "cdjeez", "--force", "--reinstall"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "cdjeez"],
                capture_output=True, text=True, timeout=120,
            )
        if result.returncode != 0:
            logger.error("Auto-update failed: %s", result.stderr or result.stdout)
            for f in (ENV_FILE, STATE_FILE):
                backup = backup_dir / f.name
                if backup.exists():
                    shutil.copy2(backup, f)
            return False
        ok("Package upgraded")
    except subprocess.TimeoutExpired:
        logger.error("Auto-update timed out")
        return False

    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            state = _migrate_state(state)
            STATE_FILE.write_text(json.dumps(state, indent=2))
            ok("State migrated")
        except Exception as e:
            logger.warning("State migration failed: %s", e)

    try:
        _migrate_env()
        ok("Config migrated")
    except Exception as e:
        logger.warning("Config migration failed: %s", e)

    try:
        register_launchdaemon()
        ok("LaunchAgent regenerated")
    except Exception as e:
        logger.warning("Could not regenerate LaunchAgent: %s", e)

    ok(f"Updated to v{target_version}")
    return True


def check_for_updates() -> str | None:
    return _get_latest_version()


def run_update(check_only: bool = False) -> None:
    current = _get_installed_version()
    latest = _get_latest_version()

    if latest is None:
        console.print()
        warn("Could not check for updates.")
        dim(f"Current version: v{current}")
        dim("Make sure you have internet connectivity and try again.")
        console.print()
        sys.exit(1)

    console.print()
    boxed('CDJeez Update', f'Current: v{current}\nLatest:    v{latest}')
    console.print()

    if current == latest:
        ok("Already up to date!")
        dim("Your CDJs are still overpriced though.")
        console.print()
        return

    if check_only:
        dim(f"Update available: v{current} -> v{latest}")
        dim("Run 'cdjeez update' to install.")
        console.print()
        return

    dim(f"Updating v{current} -> v{latest}...")
    console.print()

    step(1, 6, "Backing up config...")
    backup_dir = _backup_config()
    ok(f"Config backed up to {backup_dir}")

    step(2, 6, "Stopping daemon...")
    stopped = request_stop(timeout=60)
    if stopped:
        ok("Daemon stopped gracefully")
    else:
        warn("Daemon did not respond, force-killing...")
        kill_running_daemon()
        ok("Daemon force-stopped")

    step(3, 6, "Unloading LaunchAgent...")
    for plist in (INSTALLED_PLIST,):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
    ok("LaunchAgent unloaded")

    step(4, 6, "Upgrading package...")
    try:
        result = subprocess.run(
            ["uv", "tool", "install", "cdjeez", "--force", "--reinstall"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "cdjeez"],
                capture_output=True, text=True, timeout=120,
            )
        if result.returncode != 0:
            fail("Upgrade failed")
            dim(result.stderr or result.stdout)
            dim("Restoring from backup...")
            for f in (ENV_FILE, STATE_FILE):
                backup = backup_dir / f.name
                if backup.exists():
                    shutil.copy2(backup, f)
            sys.exit(1)
        ok("Package upgraded")
    except subprocess.TimeoutExpired:
        fail("Upgrade timed out")
        dim("Restoring from backup...")
        for f in (ENV_FILE, STATE_FILE):
            backup = backup_dir / f.name
            if backup.exists():
                shutil.copy2(backup, f)
        sys.exit(1)

    step(5, 6, "Migrating data...")
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            state = _migrate_state(state)
            STATE_FILE.write_text(json.dumps(state, indent=2))
            ok("State migrated")
        except Exception as e:
            logger.warning("State migration failed: %s", e)
            warn("State migration had issues, restored from backup")

    try:
        _migrate_env()
        ok("Config migrated")
    except Exception as e:
        logger.warning("Config migration failed: %s", e)
        warn("Config migration had issues (manual review recommended)")

    step(6, 6, "Restarting daemon...")
    try:
        register_launchdaemon()
        ok("LaunchAgent regenerated and loaded")
    except Exception as e:
        logger.warning("Could not regenerate LaunchAgent: %s", e)
        if INSTALLED_PLIST.exists():
            subprocess.run(["launchctl", "load", str(INSTALLED_PLIST)], capture_output=True, check=False)
            ok("LaunchAgent reloaded")
        else:
            warn("Could not register LaunchAgent. Run 'cdjeez setup' to fix.")

    new_version = _get_installed_version()
    console.print()
    boxed('Update Complete!', f'v{current} -> v{new_version}')
    console.print()
