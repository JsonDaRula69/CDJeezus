"""Interactive setup wizard for CDJeezus.

Because paying $2000 for a deck without Stems is a lifestyle choice,
and we're here to make it slightly less painful.
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
from .style import (
    banner, step, ok, warn, fail, dim, info, accent, separator,
    boxed, summary_box, disclaimer_box,
    select, multiselect, confirm, password, text_input, press_enter,
    console,
)

logger = logging.getLogger(__name__)

ENV_FILE = CONFIG_DIR / ".env"
INSTALLED_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.cdjeezus.plist"
LEGACY_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.djtchill.cdjeezus.plist"
STREAMFLACR_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.streamflacr.plist"
STREAMFLACR_LEGACY_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.djtchill.streamflacr.plist"

_OLD_STREAMFLACR_CONFIG = Path.home() / ".config" / "streamflacr"


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


# ── Detection ────────────────────────────────────────────────────────

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
    warn("SoundCloud login not detected in Chrome.")
    dim("Opening SoundCloud login page in your browser...")
    subprocess.run(["open", "https://soundcloud.com/signin"], check=False)
    press_enter("Press Enter once you've logged into SoundCloud in Chrome...")


# ── LaunchAgent / Env ─────────────────────────────────────────────────

def register_launchdaemon() -> None:
    """Register the CDJeezus LaunchAgent for auto-start on login."""
    python = sys.executable
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cdjeezus</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>cdjeezus</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_FILE}</string>
</dict>
</plist>
"""
    INSTALLED_PLIST.parent.mkdir(parents=True, exist_ok=True)
    INSTALLED_PLIST.write_text(plist_content)
    subprocess.run(["launchctl", "load", str(INSTALLED_PLIST)], capture_output=True, check=False)


def write_env_file(config: dict) -> None:
    """Write the .env config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    primary = config.get("primary_dj", "serato")
    download_dir = str(DOWNLOAD_DIR) if primary == "serato" else str(
        Path.home() / "Music" / "rekordbox_auto_import"
    )
    env_content = f"""# CDJeezus configuration — generated by setup wizard
# Edit at your own risk. I'm a config file, not a cop.

SLSK_USERNAME={config.get("slsk_username", "")}
SLSK_PASSWORD={config.get("slsk_password", "")}
SOUNDCLOUD_USER_URL={config.get("user_url", "")}
PRIMARY_DJ={primary}
TWO_WAY_SYNC={"1" if config.get("two_way_sync") else "0"}
DOWNLOAD_DIR={download_dir}
PLAYLIST_MODE={config.get("playlist_mode", "all")}
MONITORED_PLAYLISTS={",".join(config.get("monitored_playlists", []))}
BACKUP_ENABLED={"1" if config.get("backup_enabled") else "0"}
BACKUP_SERATO={"1" if config.get("backup_serato") else "0"}
BACKUP_REKORDBOX={"1" if config.get("backup_rekordbox") else "0"}
ACOUSTID_API_KEY={config.get("acoustid_api_key", "")}
FINGERPRINT_VERIFY={"1" if config.get("fingerprint_verify", True) else "0"}
UPSCALE_ENABLED={"1" if config.get("upscale_enabled", False) else "0"}
AUTO_UPDATE_INTERVAL=14400
SOUNDCLOUD_POLL_INTERVAL=300
SEARCH_TIMEOUT=30
SERATO_CHECK_INTERVAL=30
"""
    ENV_FILE.write_text(env_content)


# ── Uninstall ─────────────────────────────────────────────────────────

def full_uninstall() -> None:
    """Remove all CDJeezus artifacts. Music files, libraries, and backups stay."""
    from . import __version__

    console.print()
    boxed(f'CDJeezus v{__version__} — Uninstall', 'Time to cleanse the temple.')
    console.print()

    from .daemon import request_stop, is_running
    if is_running():
        dim("Stopping daemon...")
        stopped = request_stop(timeout=60)
        if stopped:
            ok("Daemon stopped gracefully")
        else:
            kill_running_daemon()
            ok("Daemon force-stopped")

    removed_plist = False
    for plist in (INSTALLED_PLIST, LEGACY_PLIST, STREAMFLACR_PLIST, STREAMFLACR_LEGACY_PLIST):
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
            plist.unlink()
            removed_plist = True
    if removed_plist:
        ok("LaunchAgent removed")
    else:
        dim("No LaunchAgent found")

    if CONFIG_DIR.exists():
        shutil.rmtree(CONFIG_DIR, ignore_errors=True)
        ok("Config and staging removed")
    else:
        dim("No config directory found")

    for f in (PID_FILE, LOG_FILE):
        if f.exists() and not str(f).startswith(str(CONFIG_DIR)):
            f.unlink(missing_ok=True)

    console.print()
    separator()
    dim("Music files, DJ libraries, and backups were NOT modified.")
    dim("As it should be. We're not monsters.")
    separator()
    console.print()


# ── Setup Wizard ──────────────────────────────────────────────────────

def run_setup(*, non_interactive: bool = False) -> None:
    """Run the interactive setup wizard. 8 steps to DJ salvation."""
    from . import __version__

    config: dict = {}

    console.print()
    boxed('CDJeezus Setup Wizard', f'v{__version__} — 8 steps to DJ salvation')
    console.print()

    # Migrate from StreamFLACr if needed
    if _OLD_STREAMFLACR_CONFIG.exists() and not CONFIG_DIR.exists():
        dim("Migrating config from StreamFLACr to CDJeezus...")
        try:
            _OLD_STREAMFLACR_CONFIG.rename(CONFIG_DIR)
            ok("Migrated config from StreamFLACr to CDJeezus")
        except Exception as e:
            logger.warning("Could not auto-migrate: %s", e)
        console.print()

    # ── Step 1: Primary DJ ──
    serato_found = detect_serato()
    rekordbox_found = detect_rekordbox()
    step(1, 8, 'Choosing your religion...')

    if serato_found and rekordbox_found:
        dim("Both detected. Pick your primary (the one you actually mix on):")
        if not non_interactive:
            choice = select("Which one do you suffer with most?", ["Serato DJ", "Rekordbox"])
            config["primary_dj"] = "serato" if choice == 0 else "rekordbox"
        else:
            config["primary_dj"] = "serato"
    elif serato_found:
        ok("Serato DJ auto-detected")
        config["primary_dj"] = "serato"
    elif rekordbox_found:
        ok("Rekordbox auto-detected")
        config["primary_dj"] = "rekordbox"
    else:
        warn("No DJ software detected")
        dim("CDJs and laptops don't count. Install Serato or Rekordbox first.")
        if not non_interactive:
            choice = select("Which are you installing?", ["Serato DJ", "Rekordbox"])
            config["primary_dj"] = "serato" if choice == 0 else "rekordbox"
        else:
            config["primary_dj"] = "serato"
    console.print()

    # ── Step 2: Secondary DJ / 2-way sync ──
    step(2, 8, 'Checking for the other cult...')
    secondary = "rekordbox" if config["primary_dj"] == "serato" else "serato"
    secondary_found = detect_rekordbox() if config["primary_dj"] == "serato" else detect_serato()

    if secondary_found:
        ok(f"{secondary.title()} detected!")
        if not non_interactive:
            config["two_way_sync"] = confirm(f"Enable 2-way sync with {secondary.title()}?", default=False)
        else:
            config["two_way_sync"] = False

        if config["two_way_sync"]:
            ok(f"2-way sync with {secondary.title()} enabled")
        else:
            dim("2-way sync disabled. More crates, more problems.")
    else:
        warn(f"{secondary.title()} not detected!")
        dim("Library sync disabled. Different club, same cult.")
        config["two_way_sync"] = False
        if not non_interactive:
            press_enter()
    console.print()

    # ── Step 3: Soulseek ──
    step(3, 8, 'Soulseek setup...')
    if detect_soulseek_installation():
        ok("SoulseekQt.app found")
    else:
        warn("SoulseekQt.app not found")
        dim("It's recommended but not required. The built-in client works too.")
        if not non_interactive and confirm("Install SoulseekQt?", default=False):
            dim("Downloading from slsknet.org...")
            try:
                subprocess.run(["open", "https://www.slsknet.org/download"], check=False)
            except Exception:
                pass
            press_enter("Press Enter once you've installed it...")

    if detect_soulseek_data():
        ok("SoulseekQt data found (you've logged in before)")
    else:
        dim("No Soulseek data found")

    console.print()
    dim("Soulseek credentials required. Yes, you need an account.")
    dim("If you don't have one, visit https://www.slsknet.org")
    console.print()
    config["slsk_username"] = text_input("Soulseek username")
    config["slsk_password"] = password("Soulseek password")
    console.print()

    # ── Step 4: AcoustID ──
    step(4, 8, 'Audio fingerprinting...')
    fpcalc_available = detect_fpcalc()
    if fpcalc_available:
        ok("fpcalc (chromaprint) found — audio fingerprinting enabled")
    else:
        warn("fpcalc not found")
        dim("Run `brew install chromaprint` unless you enjoy guessing")
    if not non_interactive:
        acoustid_key = text_input("AcoustID API key (press Enter to skip)")
        if acoustid_key:
            config["acoustid_api_key"] = acoustid_key
            config["fingerprint_verify"] = True
        else:
            config["acoustid_api_key"] = ""
            config["fingerprint_verify"] = fpcalc_available
    else:
        config["acoustid_api_key"] = ""
        config["fingerprint_verify"] = fpcalc_available
    console.print()

    # ── Step 5: SoundCloud ──
    step(5, 8, 'SoundCloud connection...')
    if detect_soundcloud_login():
        user_url = extract_soundcloud_user_url()
        if user_url:
            ok("SoundCloud login detected in Chrome")
            dim(f"Profile: {user_url}")
            config["user_url"] = user_url
        else:
            warn("Could not extract SoundCloud profile from Chrome")
            if not non_interactive:
                config["user_url"] = text_input("SoundCloud profile URL")
            else:
                config["user_url"] = ""
    else:
        if not non_interactive:
            prompt_soundcloud_login()
            user_url = extract_soundcloud_user_url()
            if not user_url:
                user_url = text_input("SoundCloud profile URL")
            config["user_url"] = user_url
        else:
            config["user_url"] = ""
    console.print()

    # ── Step 6: Playlists ──
    step(6, 8, 'Playlist selection...')
    if not non_interactive:
        choice = select("All playlists or just the ones you actually use?",
                        ["All playlists", "Custom selection"])
        if choice == 0:
            config["playlist_mode"] = "all"
            config["monitored_playlists"] = []
            ok("All playlists will be monitored")
        else:
            from .soundcloud import discover_user_playlists
            playlists = discover_user_playlists()
            if playlists:
                playlist_names = [p.title for p in playlists]
                selected = multiselect("Select playlists to monitor:", playlist_names)
                config["playlist_mode"] = "custom"
                config["monitored_playlists"] = [playlists[i].url for i in selected]
                ok(f"{len(selected)} playlist(s) selected")
            else:
                warn("No playlists found")
                config["playlist_mode"] = "all"
                config["monitored_playlists"] = []
    else:
        config["playlist_mode"] = "all"
        config["monitored_playlists"] = []
        dim("Monitoring all playlists (non-interactive mode)")
    console.print()

    # ── Step 7: Backups ──
    step(7, 8, 'Library backups...')
    if not non_interactive:
        config["backup_enabled"] = confirm("Enable library backups?", default=False)
    else:
        config["backup_enabled"] = False

    if config["backup_enabled"]:
        backup_options = []
        if detect_serato():
            backup_options.append("Serato")
        if detect_rekordbox():
            backup_options.append("Rekordbox")

        if backup_options and not non_interactive:
            selected = multiselect("Which libraries to back up?", backup_options)
            config["backup_serato"] = "Serato" in [backup_options[i] for i in selected]
            config["backup_rekordbox"] = "Rekordbox" in [backup_options[i] for i in selected]
        else:
            config["backup_serato"] = detect_serato()
            config["backup_rekordbox"] = detect_rekordbox()

        ok("Backups enabled")
    else:
        config["backup_serato"] = False
        config["backup_rekordbox"] = False
        config["backup_dir"] = str(BACKUP_DIR)
        dim("Backups disabled. Live dangerously, I guess.")
    console.print()

    # ── Step 8: Config Summary & Confirm ──
    while True:
        step(8, 8, "Here's what you're signing up for:")
        console.print()

        summary_box('Config Summary', [
            ('Primary DJ', config.get('primary_dj', 'serato').title()),
            ('2-way sync', 'Yes' if config.get('two_way_sync') else 'No'),
            ('Soulseek', config.get('slsk_username', '')),
            ('AcoustID', 'Yes' if config.get('acoustid_api_key') else 'No'),
            ('SoundCloud', config.get('user_url', '')),
            ('Playlists', config.get('playlist_mode', 'all').title()),
            ('Backups', 'Yes' if config.get('backup_enabled') else 'No'),
        ])
        console.print()

        if not non_interactive:
            if not confirm("Look good?", default=True):
                edit_options = [
                    "Primary DJ", "2-way sync", "Soulseek", "SoundCloud",
                    "Playlist selection", "Library backups", "Never mind, let's just go",
                ]
                choice = select("Which config to edit?", edit_options)
                if choice == 6:
                    break
                _edit_config_step(choice, config)
                console.print()
                continue
        break

    # ── Disclaimer ──
    console.print()
    disclaimer_box(
        "Alright, real talk: you're only supposed to use this for music "
        "you have rights to, on a private SoulSeek server that also belongs to you. "
        "This is for backup and syncing only.\n\n"
        "Also SoundCloud might get pissy if you don't have Artist Pro, so use at your own risk. "
        "I worked around it but [italic]idk ask Naveen to do better.[/italic]"
    )
    console.print()

    if not non_interactive:
        disclaimer_choice = select("Last chance to back out:", ["Agreed", "Wait, what?"])
        if disclaimer_choice == 1:
            console.print()
            boxed('', "lol. fuck off. Closing this window will uninstall automatically.\nOr press Enter to stay and accept your fate.")
            press_enter()
    console.print()

    # ── Write config ──
    write_env_file(config)
    dim(f"Config written to {ENV_FILE}")

    primary = config.get("primary_dj", "serato")
    download_dir = str(DOWNLOAD_DIR) if primary == "serato" else str(
        Path.home() / "Music" / "rekordbox_auto_import"
    )
    ok(f"Download directory: {download_dir}")
    ok(f"Staging directory: {STAGING_DIR}")

    if config.get("backup_enabled"):
        from .backup import run_backups
        results = run_backups(
            backup_serato=config.get("backup_serato", False),
            backup_rekordbox=config.get("backup_rekordbox", False),
        )
        if results:
            ok(f"Initial backup created ({len(results)} archive(s))")

    if not non_interactive:
        if confirm("Start CDJeezus automatically on login?", default=True):
            register_launchdaemon()
            ok("LaunchDaemon registered")
        else:
            dim("Skipping daemon. Run `cdjeezus --daemon` to start manually.")
    else:
        register_launchdaemon()

    console.print()
    boxed('Setup Complete!', 'Deploying the daemon in 3...')

    import time
    time.sleep(3)
    console.print()


def _edit_config_step(step: int, config: dict) -> None:
    """Re-run a specific setup step to edit config."""
    if step == 0:  # Primary DJ
        options = []
        if detect_serato():
            options.append("Serato DJ (detected)")
        else:
            options.append("Serato DJ (not found)")
        if detect_rekordbox():
            options.append("Rekordbox (detected)")
        else:
            options.append("Rekordbox (not found)")
        choice = select("Select your primary DJ software:", options)
        config["primary_dj"] = "serato" if choice == 0 else "rekordbox"
    elif step == 1:  # 2-way sync
        secondary = "rekordbox" if config["primary_dj"] == "serato" else "serato"
        config["two_way_sync"] = confirm(f"Enable 2-way sync with {secondary.title()}?", default=False)
    elif step == 2:  # Soulseek
        dim("Soulseek credentials required. Yes, you need an account.")
        config["slsk_username"] = text_input("Soulseek username")
        config["slsk_password"] = password("Soulseek password")
    elif step == 3:  # AcoustID
        if detect_fpcalc():
            ok("fpcalc (chromaprint) is installed")
        else:
            warn("fpcalc not found")
            dim("Run `brew install chromaprint` unless you enjoy guessing")
        config["acoustid_api_key"] = text_input("AcoustID API key (press Enter to skip)")
    elif step == 4:  # SoundCloud
        if not detect_soundcloud_login():
            prompt_soundcloud_login()
        user_url = extract_soundcloud_user_url()
        if not user_url:
            user_url = text_input("SoundCloud profile URL")
        config["user_url"] = user_url
    elif step == 5:  # Playlists
        choice = select("All playlists or just the ones you actually use?",
                        ["All playlists", "Custom selection"])
        if choice == 0:
            config["playlist_mode"] = "all"
            config["monitored_playlists"] = []
        else:
            from .soundcloud import discover_user_playlists
            playlists = discover_user_playlists()
            if playlists:
                playlist_names = [p.title for p in playlists]
                selected = multiselect("Select playlists:", playlist_names)
                config["playlist_mode"] = "custom"
                config["monitored_playlists"] = [playlists[i].url for i in selected]
    elif step == 6:  # Backups
        config["backup_enabled"] = confirm("Enable library backups?", default=False)
        if config["backup_enabled"]:
            backup_options = []
            if detect_serato():
                backup_options.append("Serato")
            if detect_rekordbox():
                backup_options.append("Rekordbox")
            if backup_options:
                selected = multiselect("Which libraries to back up?", backup_options)
                config["backup_serato"] = "Serato" in [backup_options[i] for i in selected]
                config["backup_rekordbox"] = "Rekordbox" in [backup_options[i] for i in selected]


def _launch_soundcloud_app() -> None:
    """Launch SoundCloud PWA app to refresh OAuth token."""
    sc_app = Path.home() / "Applications" / "Chrome Apps.localized" / "SoundCloud.app"
    if sc_app.exists():
        subprocess.run(["open", str(sc_app)], check=False)
    else:
        subprocess.run(["open", "-a", "Google Chrome", "https://soundcloud.com"], check=False)
