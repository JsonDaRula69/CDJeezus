"""Configuration management via environment / .env file."""

import os
from pathlib import Path
from dotenv import load_dotenv

# User-facing config directory (XDG standard)
CONFIG_DIR: Path = Path(os.environ.get("CDJEEZ_CONFIG_DIR", str(Path.home() / ".config" / "cdjeez")))

# Load .env from config dir first, then fall back to CWD for dev
_env_file = CONFIG_DIR / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    load_dotenv()  # fallback: .env in current working directory (dev mode)

# Soulseek credentials
SLSK_USERNAME: str = os.environ.get("SLSK_USERNAME", "")
SLSK_PASSWORD: str = os.environ.get("SLSK_PASSWORD", "")

# SoundCloud
SOUNDCLOUD_USER_URL: str = os.environ.get("SOUNDCLOUD_USER_URL", "")
SOUNDCLOUD_POLL_INTERVAL: int = int(os.environ.get("SOUNDCLOUD_POLL_INTERVAL", "300"))

# Primary DJ software: "serato" or "rekordbox"
PRIMARY_DJ: str = os.environ.get("PRIMARY_DJ", "serato")

# Two-way sync between Serato and Rekordbox
TWO_WAY_SYNC: bool = os.environ.get("TWO_WAY_SYNC", "0") == "1"

# Download destination (primary DJ's auto-import folder)
DOWNLOAD_DIR: Path = Path(os.environ.get("DOWNLOAD_DIR", str(Path.home() / "Music" / "_Serato_" / "Auto Import")))

# Staging directory — files download and get tagged here before moving to DOWNLOAD_DIR
STAGING_DIR: Path = Path(os.environ.get("STAGING_DIR", str(CONFIG_DIR / "staging")))

# Serato
SERATO_DIR: Path = Path(os.environ.get("SERATO_DIR", str(Path.home() / "Music" / "_Serato_")))

# Rekordbox
REKORDBOX_DIR: Path = Path(os.environ.get("REKORDBOX_DIR", str(Path.home() / "Library" / "Pioneer" / "rekordbox")))

# Backup settings
BACKUP_ENABLED: bool = os.environ.get("BACKUP_ENABLED", "0") == "1"
BACKUP_SERATO: bool = os.environ.get("BACKUP_SERATO", "0") == "1"
BACKUP_REKORDBOX: bool = os.environ.get("BACKUP_REKORDBOX", "0") == "1"
BACKUP_DIR: Path = Path(os.environ.get("BACKUP_DIR", str(Path.home() / "Music" / "LibraryBackups")))

# Playlist monitoring: "all" or comma-separated list of playlist URLs
PLAYLIST_MODE: str = os.environ.get("PLAYLIST_MODE", "all")
MONITORED_PLAYLISTS: list[str] = [
    u.strip() for u in os.environ.get("MONITORED_PLAYLISTS", "").split(",") if u.strip()
]

# State file
STATE_FILE: Path = Path(os.environ.get("STATE_FILE", str(CONFIG_DIR / "state.json")))

# Daemon lifecycle files
PID_FILE: Path = Path(os.environ.get("CDJEEZ_PID_FILE", str(CONFIG_DIR / "cdjeez.pid")))
STOP_FILE: Path = Path(os.environ.get("CDJEEZ_STOP_FILE", str(CONFIG_DIR / "stop-requested")))
LOG_FILE: Path = Path(os.environ.get("CDJEEZ_LOG_FILE", str(CONFIG_DIR / "cdjeez.log")))

# Search preferences
SEARCH_TIMEOUT: int = int(os.environ.get("SEARCH_TIMEOUT", "30"))
PREFER_FREE_SLOTS: bool = os.environ.get("PREFER_FREE_SLOTS", "1") == "1"
MIN_FILESIZE_MB: int = int(os.environ.get("MIN_FILESIZE_MB", "5"))

# Serato awareness
SERATO_CHECK_INTERVAL: int = int(os.environ.get("SERATO_CHECK_INTERVAL", "30"))

# Audio fingerprinting
ACOUSTID_API_KEY: str = os.environ.get("ACOUSTID_API_KEY", "")
FINGERPRINT_VERIFY: bool = os.environ.get("FINGERPRINT_VERIFY", "1") == "1"

# Library upscaling (replace low-quality files with higher quality)
UPSCALE_ENABLED: bool = os.environ.get("UPSCALE_ENABLED", "0") == "1"

# Auto-update: check PyPI for new versions on startup and every N seconds
AUTO_UPDATE_INTERVAL: int = int(os.environ.get("AUTO_UPDATE_INTERVAL", "14400"))  # 4 hours


def is_configured() -> bool:
    """Check if the minimum required configuration is present."""
    return bool(SLSK_USERNAME and SLSK_PASSWORD and SOUNDCLOUD_USER_URL)


def get_primary_dj() -> str:
    """Return the primary DJ software name."""
    return PRIMARY_DJ


def get_secondary_dj() -> str | None:
    """Return the secondary DJ software if two-way sync is enabled."""
    if not TWO_WAY_SYNC:
        return None
    return "rekordbox" if PRIMARY_DJ == "serato" else "serato"
