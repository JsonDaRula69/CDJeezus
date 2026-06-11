"""Interactive setup wizard for CDJeezus.

Detects DJ software, SoundCloud login, and Soulseek installation,
configures playlist monitoring and library backups, writes .env,
and registers the launchd daemon.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import (
    CONFIG_DIR, DOWNLOAD_DIR, SERATO_DIR, REKORDBOX_DIR,
    STAGING_DIR, BACKUP_DIR, PID_FILE, STOP_FILE, LOG_FILE,
)

logger = logging.getLogger(__name__)

ENV_FILE = CONFIG_DIR / ".env"
INSTALLED_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.cdjeezus.plist"
LEGACY_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.djtchill.cdjeezus.plist"
# Old StreamFLACr plists from before the rename
STREAMFLACR_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.streamflacr.plist"
STREAMFLACR_LEGACY_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.djtchill.streamflacr.plist"


def kill_running_daemon() -> bool:
    """Kill any stale cdjeezus daemon from a previous run."""
    import signal
    for plist in (INSTALLED_PLIST, LEGACY_PLIST, STREAMFLACR_PLIST, STREAMFLACR_LEGACY_PLIST):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)

    my_pid = os.getpid()
    parent_pid = os.getppid()
    killed = False
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python.*(cdjeezus|streamflacr)"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for pid_str in result.stdout.strip().split("\n"):
                try:
                    pid = int(pid_str.strip())
                    if pid in (my_pid, parent_pid):
                        continue
                    os.kill(pid, signal.SIGTERM)
                    killed = True
                except (ValueError, ProcessLookupError, PermissionError):
                    pass
    except Exception:
        pass
    if killed:
        import time
        time.sleep(1)
    return killed


# ── DJ Software Detection ────────────────────────────────────────────

def detect_serato() -> bool:
    return SERATO_DIR.exists()


def detect_rekordbox() -> bool:
    return REKORDBOX_DIR.exists() and (REKORDBOX_DIR / "master.db").exists()


def detect_fpcalc() -> bool:
    try:
        result = subprocess.run(["fpcalc", "-version"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def detect_soulseek_installation() -> bool:
    for path in [Path("/Applications/SoulseekQt.app"), Path.home() / "Applications" / "SoulseekQt.app"]:
        if path.exists():
            return True
    return False


def detect_soulseek_data() -> bool:
    data_dir = Path.home() / ".SoulseekQt"
    return data_dir.exists() and any(data_dir.iterdir())


# ── SoundCloud Detection ──────────────────────────────────────────────

def detect_soundcloud_login() -> bool:
    from .soundcloud import has_oauth
    return has_oauth()


def extract_soundcloud_user_url() -> str | None:
    from .soundcloud import _api_get
    try:
        me = _api_get("me")
        if me:
            return me.get("permalink_url")
    except Exception as e:
        logger.debug("Could not get SoundCloud user URL: %s", e)
    return None


def prompt_soundcloud_login() -> None:
    print("\n  SoundCloud login not detected in Chrome.")
    print("  Opening SoundCloud login page in your browser...")
    subprocess.run(["open", "https://soundcloud.com/signin"], check=False)
    input("  Press Enter once you've logged into SoundCloud in Chrome... ")


# ── TUI Helpers ───────────────────────────────────────────────────────

def _menu_select(options: list[str], title: str = "") -> int:
    """Single-select menu using arrow keys and Enter."""
    from simple_term_menu import TerminalMenu
    menu = TerminalMenu(options, title=title)
    return menu.show()


def _multi_select(options: list[str], title: str = "") -> list[int]:
    """Multi-select menu using arrow keys, spacebar, and Enter."""
    from simple_term_menu import TerminalMenu
    menu = TerminalMenu(
        options,
        title=title,
        multi_select=True,
        show_multi_select_hint=True,
    )
    result = menu.show()
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


# ── Soulseek Prompt ────────────────────────────────────────────────────

def prompt_soulseek_setup() -> dict:
    print("\n  Soulseek credentials required for downloading files.")
    print("  If you don't have an account, visit https://www.slsknet.org\n")
    username = ""
    while not username:
        username = input("  Soulseek username: ").strip()
    password = ""
    while not password:
        password = input("  Soulseek password: ").strip()
    return {"username": username, "password": password}


# ── .env File ─────────────────────────────────────────────────────────

def write_env_file(config: dict) -> None:
    """Write all config selections to .env."""
    content = f"""\
# CDJeezus configuration
# Generated by setup wizard

# Primary DJ software (serato or rekordbox)
PRIMARY_DJ={config.get('primary_dj', 'serato')}

# Two-way sync between DJ libraries
TWO_WAY_SYNC={'1' if config.get('two_way_sync') else '0'}

# Soulseek credentials
SLSK_USERNAME={config.get('slsk_username', '')}
SLSK_PASSWORD={config.get('slsk_password', '')}

# SoundCloud user URL
SOUNDCLOUD_USER_URL={config.get('user_url', '')}

# Playlist monitoring (all or custom)
PLAYLIST_MODE={config.get('playlist_mode', 'all')}
MONITORED_PLAYLISTS={','.join(config.get('monitored_playlists', []))}

# Library backups
BACKUP_ENABLED={'1' if config.get('backup_enabled') else '0'}
BACKUP_SERATO={'1' if config.get('backup_serato') else '0'}
BACKUP_REKORDBOX={'1' if config.get('backup_rekordbox') else '0'}
BACKUP_DIR={config.get('backup_dir', str(BACKUP_DIR))}

# Download directory
DOWNLOAD_DIR={config.get('download_dir', str(DOWNLOAD_DIR))}

# Staging directory
STAGING_DIR={STAGING_DIR}
SERATO_DIR={SERATO_DIR}
REKORDBOX_DIR={REKORDBOX_DIR}

# Search preferences
SEARCH_TIMEOUT=30
PREFER_FREE_SLOTS=1
MIN_FILESIZE_MB=5

# Audio fingerprinting
ACOUSTID_API_KEY={config.get('acoustid_api_key', '')}
FINGERPRINT_VERIFY=1

# Library upscaling
UPSCALE_ENABLED=0

# Auto-update interval (seconds, default 4 hours)
AUTO_UPDATE_INTERVAL=14400
"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(content)
    print(f"\n  Config written to {ENV_FILE}")


# ── LaunchDaemon ──────────────────────────────────────────────────────

def _generate_plist() -> str:
    python_path = sys.executable
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cdjeezus</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>cdjeezus</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_FILE}</string>
    <key>WorkingDirectory</key>
    <string>{Path.home()}</string>
</dict>
</plist>
"""


def register_launchdaemon() -> bool:
    plist_content = _generate_plist()
    INSTALLED_PLIST.parent.mkdir(parents=True, exist_ok=True)
    INSTALLED_PLIST.write_text(plist_content)

    # Unload old plist if present (including StreamFLACr legacy)
    for plist in (INSTALLED_PLIST, LEGACY_PLIST, STREAMFLACR_PLIST, STREAMFLACR_LEGACY_PLIST):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)

    # Remove legacy plists
    if LEGACY_PLIST.exists():
        LEGACY_PLIST.unlink()
    if STREAMFLACR_PLIST.exists():
        STREAMFLACR_PLIST.unlink()
    if STREAMFLACR_LEGACY_PLIST.exists():
        STREAMFLACR_LEGACY_PLIST.unlink()

    result = subprocess.run(
        ["launchctl", "load", str(INSTALLED_PLIST)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        logger.info("LaunchDaemon registered: %s", INSTALLED_PLIST)
        return True
    logger.error("Failed to register LaunchDaemon: %s", result.stderr)
    return False


def unregister_launchdaemon() -> bool:
    for plist in (INSTALLED_PLIST, LEGACY_PLIST, STREAMFLACR_PLIST, STREAMFLACR_LEGACY_PLIST):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
            plist.unlink()
    logger.info("LaunchDaemon unregistered")
    return True


# ── Uninstall ──────────────────────────────────────────────────────────

def full_uninstall() -> None:
    """Interactive uninstall. Asks about keeping streaming source migration data.

    Removes all CDJeezus artifacts: config, staging, logs, LaunchAgent.
    Never touches Serato or Rekordbox library data.
    The only prompt asks whether to keep migration data (state.json, .env).
    """
    from . import __version__
    print(f"\n  CDJeezus v{__version__} Uninstall\n")

    # Stop daemon
    from .daemon import request_stop
    request_stop(timeout=30)

    # Unregister daemon
    unregister_launchdaemon()

    # Ask about migration data
    keep_data = input("  Keep streaming source migration data? [Y/n]: ").strip().lower()
    keep_data = keep_data in ("", "y", "yes")

    # Remove config directory
    if CONFIG_DIR.exists():
        if keep_data:
            # Keep state.json and .env (migration data), remove everything else
            for item in list(CONFIG_DIR.iterdir()):
                if item.name in ("state.json", ".env"):
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            print("  ✓ Config removed (kept state.json and .env)")
        else:
            shutil.rmtree(CONFIG_DIR, ignore_errors=True)
            print("  ✓ All config and migration data removed")

    # Remove plist (current, legacy, and old StreamFLACr names)
    for plist in (INSTALLED_PLIST, LEGACY_PLIST, STREAMFLACR_PLIST, STREAMFLACR_LEGACY_PLIST):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
            plist.unlink()
    print("  ✓ LaunchAgent removed")

    # NOTE: We do NOT remove backup directories. These are safety nets
    # for the user's DJ library data and must survive uninstall.
    # The user can manually delete them if desired.

    print("  ✓ Daemon stopped")
    print()
    print("  Backups were NOT removed (~/Music/LibraryBackups).")
    print("  Serato and Rekordbox libraries were NOT modified.")
    print()


# ── Main Wizard ────────────────────────────────────────────────────────


def _migrate_streamflacr_config() -> None:
    """Migrate config from a previous StreamFLACr installation.

    If ~/.config/streamflacr/ exists but ~/.config/cdjeezus/ does not,
    rename the directory and update the LaunchAgent plist.
    """
    old_config = Path.home() / ".config" / "streamflacr"
    new_config = Path.home() / ".config" / "cdjeezus"

    if old_config.exists() and not new_config.exists():
        logger.info("Migrating StreamFLACr config to CDJeezus: %s → %s", old_config, new_config)
        try:
            old_config.rename(new_config)
            # Update .env references (env var names changed)
            env_file = new_config / ".env"
            if env_file.exists():
                content = env_file.read_text()
                content = content.replace("STREAMFLACR_", "CDJEEZUS_")
                env_file.write_text(content)
            print("  ✓ Migrated config from StreamFLACr to CDJeezus")
        except Exception as e:
            logger.warning("Could not migrate StreamFLACr config: %s", e)

    # Unload old StreamFLACr LaunchAgents
    for old_plist in (STREAMFLACR_PLIST, STREAMFLACR_LEGACY_PLIST):
        if old_plist.exists():
            subprocess.run(["launchctl", "unload", str(old_plist)], capture_output=True, check=False)
            try:
                old_plist.unlink()
                logger.info("Removed old plist: %s", old_plist)
            except Exception:
                pass

def run_setup(*, non_interactive: bool = False) -> None:
    """Run the full interactive setup wizard."""
    # Migrate config from old StreamFLACr install if present
    _migrate_streamflacr_config()

    config: dict = {}

    print()
    print("  ───────────────────────────────────────────")
    print("   CDJeezus Setup Wizard")
    print("  ───────────────────────────────────────────")
    print()

    # ── Step 1: Primary DJ Software ──
    serato_found = detect_serato()
    rekordbox_found = detect_rekordbox()

    if serato_found and not rekordbox_found:
        primary = "serato"
        print("  [1/8] Primary DJ: Serato DJ (auto-detected)")
    elif rekordbox_found and not serato_found:
        primary = "rekordbox"
        print("  [1/8] Primary DJ: Rekordbox (auto-detected)")
    elif not non_interactive:
        print("  [1/8] Which is your primary DJ library?")
        options = []
        if serato_found:
            options.append("Serato DJ (detected)")
        else:
            options.append("Serato DJ (not found)")
        if rekordbox_found:
            options.append("Rekordbox (detected)")
        else:
            options.append("Rekordbox (not found)")
        choice = _menu_select(options, title="  Select your primary DJ software:")
        primary = "serato" if choice == 0 else "rekordbox"
    else:
        primary = os.environ.get("PRIMARY_DJ", "serato")
        print(f"  [1/8] Primary DJ: {primary.title()} (from config)")
    config["primary_dj"] = primary
    print(f"  ✓ Primary: {primary.title()}")

    # ── Step 2: Secondary DJ & Two-Way Sync ──
    print("\n  [2/8] Checking for secondary DJ library...")
    secondary = "rekordbox" if primary == "serato" else "serato"
    secondary_found = detect_rekordbox() if secondary == "rekordbox" else detect_serato()

    if secondary_found:
        print(f"  ✓ {secondary.title()} detected!")
        if not non_interactive:
            answer = input(f"  Enable 2-way sync with {secondary.title()}? [y/N]: ").strip().lower()
            config["two_way_sync"] = answer in ("y", "yes")
        else:
            config["two_way_sync"] = os.environ.get("TWO_WAY_SYNC", "0") == "1"
        if config["two_way_sync"]:
            print(f"  ✓ 2-way sync with {secondary.title()} enabled")
        else:
            print(f"  ✗ 2-way sync disabled")
    else:
        print(f"  ✗ {secondary.title()} not detected!")
        print(f'  Library sync will be disabled, which is kinda dumb cause damn I')
        print(f'  worked so hard on that shit but whatever do you I guess.')
        print(f'  Press Enter, let\'s keep going you broke degenerate')
        config["two_way_sync"] = False
        if not non_interactive:
            input()

    # Set download dir based on primary
    if primary == "serato":
        config["download_dir"] = str(SERATO_DIR / "Auto Import")
    else:
        config["download_dir"] = str(Path.home() / "Music" / "RekordboxAutoImport")

    # ── Step 3: Soulseek ──
    print("\n  [3/8] Soulseek setup...")
    slsk_installed = detect_soulseek_installation()
    slsk_has_data = detect_soulseek_data()
    if slsk_installed:
        print("  ✓ SoulseekQt.app found")
    else:
        print("  ✗ SoulseekQt.app not found in Applications folder")
        print("  SoulseekQt is recommended but not required (the built-in client works too).")
        if not non_interactive:
            answer = input("  Install SoulseekQt? [y/N]: ").strip().lower()
            if answer == "y":
                subprocess.run(["open", "https://www.slsknet.org/download"], check=False)
                input("  Press Enter once SoulseekQt is installed... ")
    if slsk_has_data:
        print("  ✓ SoulseekQt data found (you've logged in before)")
    else:
        print("  Note: No SoulseekQt login data found.")
    if non_interactive:
        config["slsk_username"] = os.environ.get("SLSK_USERNAME", "")
        config["slsk_password"] = os.environ.get("SLSK_PASSWORD", "")
        if not config["slsk_username"] or not config["slsk_password"]:
            print("  ERROR: Non-interactive mode requires SLSK_USERNAME and SLSK_PASSWORD env vars.")
            sys.exit(1)
    else:
        slsk_creds = prompt_soulseek_setup()
        config["slsk_username"] = slsk_creds["username"]
        config["slsk_password"] = slsk_creds["password"]

    # ── Step 4: Audio Fingerprinting (AcoustID) ──
    print("\n  [4/8] Audio fingerprinting setup...")
    print("  AcoustID identifies songs by their audio fingerprint,")
    print("  letting us verify downloads match the SoundCloud track.")
    print("  This prevents downloading the wrong version (e.g. original")
    print("  mix instead of a remix). It's free for non-commercial use.")
    print("  Get your key at: https://acoustid.org/api-key")
    fpcalc_available = detect_fpcalc()
    if fpcalc_available:
        print("  ✓ fpcalc (chromaprint) found — audio fingerprinting enabled")
    else:
        print("  ✗ fpcalc not found — install chromaprint for fingerprinting: brew install chromaprint")
    if not non_interactive:
        acoustid_key = input("  AcoustID API key (press Enter to skip): ").strip()
        if acoustid_key:
            print("  ✓ AcoustID API key configured")
        else:
            print("  ✗ Skipping AcoustID (fingerprint verification will use metadata only)")
            acoustid_key = ""
    else:
        acoustid_key = os.environ.get("ACOUSTID_API_KEY", "")
    config["acoustid_api_key"] = acoustid_key

    # ── Step 5: SoundCloud ──
    print("\n  [5/8] Checking SoundCloud login...")
    sc_logged_in = detect_soundcloud_login()
    user_url = None
    if sc_logged_in:
        print("  ✓ SoundCloud login detected in Chrome")
        user_url = extract_soundcloud_user_url()
        if user_url:
            print(f"  ✓ User profile: {user_url}")
        else:
            # Try refreshing via SoundCloud app launch
            print("  Token may need refreshing, launching SoundCloud app...")
            _launch_soundcloud_app()
            import time
            time.sleep(3)
            user_url = extract_soundcloud_user_url()
            if user_url:
                print(f"  ✓ User profile: {user_url}")
    else:
        print("  ✗ SoundCloud login not found in Chrome")
        if not non_interactive:
            prompt_soundcloud_login()
            if detect_soundcloud_login():
                user_url = extract_soundcloud_user_url()

    if not user_url:
        if non_interactive:
            sys.exit(1)
        print("\n  Could not auto-detect your SoundCloud profile URL.")
        user_url = ""
        while not user_url:
            user_url = input("  SoundCloud profile URL: ").strip()
    config["user_url"] = user_url

    # ── Step 5: Playlist Selection ──
    print("\n  [6/8] Playlist monitoring...")
    if not non_interactive:
        choice = _menu_select(
            ["All playlists", "Custom selection"],
            title="  Download all playlists or custom selection?",
        )
        if choice == 0:
            config["playlist_mode"] = "all"
            config["monitored_playlists"] = []
            print("  ✓ All playlists will be monitored")
        else:
            # Fetch and display playlists for multi-select
            from .soundcloud import discover_user_playlists
            print("  Fetching your playlists...")
            playlists = discover_user_playlists()
            if not playlists:
                print("  No playlists found! Defaulting to all playlists.")
                config["playlist_mode"] = "all"
                config["monitored_playlists"] = []
            else:
                playlist_names = [p.title for p in playlists]
                selected = _multi_select(
                    playlist_names,
                    title="  Select playlists to monitor (Space to toggle, Enter to confirm):",
                )
                if not selected:
                    config["playlist_mode"] = "all"
                    config["monitored_playlists"] = []
                    print("  No playlists selected — monitoring all")
                else:
                    config["playlist_mode"] = "custom"
                    config["monitored_playlists"] = [playlists[i].url for i in selected]
                    print(f"  ✓ {len(selected)} playlist(s) selected")
    else:
        config["playlist_mode"] = os.environ.get("PLAYLIST_MODE", "all")
        config["monitored_playlists"] = [
            u.strip() for u in os.environ.get("MONITORED_PLAYLISTS", "").split(",") if u.strip()
        ]

    # ── Step 6: Library Backups ──
    print("\n  [7/8] Library backups...")
    if not non_interactive:
        answer = input("  Enable library backups? [y/N]: ").strip().lower()
        config["backup_enabled"] = answer in ("y", "yes")
    else:
        config["backup_enabled"] = os.environ.get("BACKUP_ENABLED", "0") == "1"

    if config["backup_enabled"]:
        if not non_interactive:
            # Which libraries to back up
            backup_options = []
            if detect_serato():
                backup_options.append("Serato")
            if detect_rekordbox():
                backup_options.append("Rekordbox")
            if not backup_options:
                print("  No DJ libraries detected for backup")
                config["backup_enabled"] = False
            else:
                selected = _multi_select(
                    backup_options,
                    title="  Select which libraries to back up (Space to toggle, Enter to confirm):",
                )
                config["backup_serato"] = "Serato" in [backup_options[i] for i in selected]
                config["backup_rekordbox"] = "Rekordbox" in [backup_options[i] for i in selected]
                print(f"  ✓ Backing up: {', '.join(backup_options[i] for i in selected)}")
        else:
            config["backup_serato"] = detect_serato()
            config["backup_rekordbox"] = detect_rekordbox()

        config["backup_dir"] = str(BACKUP_DIR)
    else:
        config["backup_serato"] = False
        config["backup_rekordbox"] = False
        config["backup_dir"] = str(BACKUP_DIR)
        print("  ✗ Backups disabled")

    # ── Step 7: Config Summary & Confirm ──
    while True:
        print("\n  [8/8] Configuration summary:")
        print(f"    Primary DJ:        {config.get('primary_dj', 'serato').title()}")
        print(f"    2-way sync:        {'Yes' if config.get('two_way_sync') else 'No'}")
        print(f"    Soulseek:          {config.get('slsk_username', '')}")
        print(f"    SoundCloud:        {config.get('user_url', '')}")
        print(f"    Playlists:         {config.get('playlist_mode', 'all').title()}")
        if config.get('playlist_mode') == 'custom':
            print(f"    Selected:          {len(config.get('monitored_playlists', []))} playlist(s)")
        print(f"    Backups:           {'Yes' if config.get('backup_enabled') else 'No'}")
        if config.get('backup_enabled'):
            libs = []
            if config.get('backup_serato'):
                libs.append("Serato")
            if config.get('backup_rekordbox'):
                libs.append("Rekordbox")
            print(f"    Backup libs:       {', '.join(libs)}")
        acoustid_status = "Configured" if config.get("acoustid_api_key") else "Not configured (metadata-only verification)"
        print(f"    AcoustID:          {acoustid_status}")
        print(f"    Download dir:      {config.get('download_dir', '')}")
        print()

        if not non_interactive:
            answer = input("  Look good to you? [Y/n]: ").strip().lower()
            if answer in ("", "y", "yes"):
                break
            # Let them edit specific configs
            edit_options = [
                "Primary DJ software",
                "2-way sync",
                "Soulseek credentials",
                "AcoustID API key",
                "SoundCloud",
                "Playlist selection",
                "Library backups",
                "Never mind, it's all good",
            ]
            choice = _menu_select(edit_options, title="  Which config to edit?")
            if choice == 7:  # "Never mind"
                break
            # Re-run the relevant step by looping back
            _edit_config_step(choice, config)
        else:
            break

    # ── Disclaimer ──
    print()
    print("  ───────────────────────────────────────────")
    print("  Alright bro, I'm telling you straight up that you're only")
    print("  supposed to be using this for your own music that you have")
    print("  rights to thats hosted on a private SoulSeek server that")
    print("  also belongs to you. This is for backup and syncing only")
    print("  and serves no other purpose. Also soundcloud might get")
    print("  pissy with you if you don't have an Artist Pro membership")
    print("  so use at your own risk. I think I worked around that but")
    print("  idk ask naveen to do better")
    print("  ───────────────────────────────────────────")
    print()

    if not non_interactive:
        answer = input("  Agreed / Wait What? ").strip().lower()
        if "wait" in answer:
            print()
            print("  lol fuck off.")
            print("  It'll uninstall automatically if you close the window.")
            print()
            # Wait for them to close or agree
            import select
            try:
                answer2 = input("  ...Still here? Agreed? [Y/n]: ").strip().lower()
                if answer2 not in ("", "y", "yes"):
                    # Run uninstall before exiting
                    full_uninstall()
                    sys.exit(0)
            except EOFError:
                full_uninstall()
                sys.exit(0)

    # ── Write config and register daemon ──
    write_env_file(config)

    download_dir = Path(config.get("download_dir", str(DOWNLOAD_DIR)))
    download_dir.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"  ✓ Download directory: {download_dir}")
    print(f"  ✓ Staging directory: {STAGING_DIR}")

    # Run initial backup if enabled
    if config.get("backup_enabled"):
        from .backup import run_backups
        results = run_backups(
            backup_serato=config.get("backup_serato", False),
            backup_rekordbox=config.get("backup_rekordbox", False),
        )
        if results:
            print(f"  ✓ Initial backup created ({len(results)} archive(s))")

    # Register daemon
    if not non_interactive:
        answer = input("  Start CDJeezus automatically on login? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            register_launchdaemon()
            print("  ✓ LaunchDaemon registered")
        else:
            print("  Skipping daemon registration. Run `cdjeezus --daemon` to start manually.")
    else:
        register_launchdaemon()

    print()
    print("  ───────────────────────────────────────────")
    print("  Setup complete! Deploying in 3...")
    print("  ───────────────────────────────────────────")

    import time
    time.sleep(3)
    print()


def _edit_config_step(step: int, config: dict) -> None:
    """Re-run a specific setup step to edit config."""
    if step == 0:  # Primary DJ
        serato_found = detect_serato()
        rekordbox_found = detect_rekordbox()
        options = []
        if serato_found:
            options.append("Serato DJ (detected)")
        else:
            options.append("Serato DJ (not found)")
        if rekordbox_found:
            options.append("Rekordbox (detected)")
        else:
            options.append("Rekordbox (not found)")
        choice = _menu_select(options, title="  Select your primary DJ software:")
        config["primary_dj"] = "serato" if choice == 0 else "rekordbox"
    elif step == 1:  # 2-way sync
        secondary = "rekordbox" if config["primary_dj"] == "serato" else "serato"
        answer = input(f"  Enable 2-way sync with {secondary.title()}? [y/N]: ").strip().lower()
        config["two_way_sync"] = answer in ("y", "yes")
    elif step == 2:  # Soulseek
        slsk_creds = prompt_soulseek_setup()
        config["slsk_username"] = slsk_creds["username"]
        config["slsk_password"] = slsk_creds["password"]
    elif step == 3:  # AcoustID
        fpcalc_available = detect_fpcalc()
        if fpcalc_available:
            print("  fpcalc (chromaprint) is installed.")
        else:
            print("  fpcalc not found — install chromaprint: brew install chromaprint")
        acoustid_key = input("  AcoustID API key (press Enter to skip): ").strip()
        config["acoustid_api_key"] = acoustid_key
    elif step == 4:  # SoundCloud
        if not detect_soundcloud_login():
            prompt_soundcloud_login()
        user_url = extract_soundcloud_user_url()
        if not user_url:
            user_url = input("  SoundCloud profile URL: ").strip()
        config["user_url"] = user_url
    elif step == 5:  # Playlists
        choice = _menu_select(
            ["All playlists", "Custom selection"],
            title="  Download all playlists or custom selection?",
        )
        if choice == 0:
            config["playlist_mode"] = "all"
            config["monitored_playlists"] = []
        else:
            from .soundcloud import discover_user_playlists
            playlists = discover_user_playlists()
            if playlists:
                playlist_names = [p.title for p in playlists]
                selected = _multi_select(playlist_names, title="  Select playlists:")
                config["playlist_mode"] = "custom"
                config["monitored_playlists"] = [playlists[i].url for i in selected]
    elif step == 6:  # Backups
        answer = input("  Enable library backups? [y/N]: ").strip().lower()
        config["backup_enabled"] = answer in ("y", "yes")
        if config["backup_enabled"]:
            backup_options = []
            if detect_serato():
                backup_options.append("Serato")
            if detect_rekordbox():
                backup_options.append("Rekordbox")
            if backup_options:
                selected = _multi_select(backup_options, title="  Select which libraries to back up:")
                config["backup_serato"] = "Serato" in [backup_options[i] for i in selected]
                config["backup_rekordbox"] = "Rekordbox" in [backup_options[i] for i in selected]


def _launch_soundcloud_app() -> None:
    """Launch SoundCloud PWA app to refresh OAuth token."""
    sc_app = Path.home() / "Applications" / "Chrome Apps.localized" / "SoundCloud.app"
    if sc_app.exists():
        subprocess.run(["open", str(sc_app)], check=False)
    else:
        subprocess.run(["open", "-a", "Google Chrome", "https://soundcloud.com"], check=False)
