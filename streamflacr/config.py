"""Configuration management via environment / .env file."""

import os
from pathlib import Path
from dotenv import load_dotenv

# User-facing config directory (XDG standard)
CONFIG_DIR: Path = Path(os.environ.get("STREAMFLACR_CONFIG_DIR", str(Path.home() / ".config" / "streamflacr")))

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
SOUNDCLOUD_POLL_INTERVAL: int = int(os.environ.get("SOUNDCLOUD_POLL_INTERVAL", "300"))  # seconds

# Download destination (Serato Auto Import watches this folder)
DOWNLOAD_DIR: Path = Path(os.environ.get("DOWNLOAD_DIR", str(Path.home() / "Music" / "_Serato_" / "Auto Import")))

# Staging directory — files download and get tagged here before moving to DOWNLOAD_DIR
# Located in config dir (NOT inside _Serato_) to avoid touching Serato's folder
STAGING_DIR: Path = Path(os.environ.get("STAGING_DIR", str(CONFIG_DIR / "staging")))

# Serato
SERATO_DIR: Path = Path(os.environ.get("SERATO_DIR", str(Path.home() / "Music" / "_Serato_")))

# State file (tracks last-seen set to avoid re-downloading)
STATE_FILE: Path = Path(os.environ.get("STATE_FILE", str(CONFIG_DIR / "state.json")))

# Daemon lifecycle files
PID_FILE: Path = Path(os.environ.get("STREAMFLACR_PID_FILE", str(CONFIG_DIR / "streamflacr.pid")))
STOP_FILE: Path = Path(os.environ.get("STREAMFLACR_STOP_FILE", str(CONFIG_DIR / "stop-requested")))
LOG_FILE: Path = Path(os.environ.get("STREAMFLACR_LOG_FILE", str(CONFIG_DIR / "streamflacr.log")))

# Search preferences
SEARCH_TIMEOUT: int = int(os.environ.get("SEARCH_TIMEOUT", "30"))
PREFER_FREE_SLOTS: bool = os.environ.get("PREFER_FREE_SLOTS", "1") == "1"
MIN_FILESIZE_MB: int = int(os.environ.get("MIN_FILESIZE_MB", "5"))

# Serato awareness: how often (seconds) to check if Serato is still running
SERATO_CHECK_INTERVAL: int = int(os.environ.get("SERATO_CHECK_INTERVAL", "30"))


def is_configured() -> bool:
    """Check if the minimum required configuration is present."""
    return bool(SLSK_USERNAME and SLSK_PASSWORD and SOUNDCLOUD_USER_URL)

# Audio fingerprinting (optional — enhances download verification)
# Get an API key at https://acoustid.org/api-key for AcoustID lookups
ACOUSTID_API_KEY: str = os.environ.get("ACOUSTID_API_KEY", "")

# Whether to verify downloads via audio fingerprinting (requires fpcalc)
FINGERPRINT_VERIFY: bool = os.environ.get("FINGERPRINT_VERIFY", "1") == "1"
