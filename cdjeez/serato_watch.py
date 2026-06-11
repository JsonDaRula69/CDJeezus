"""Detect whether Serato DJ is running and manage file imports accordingly.

Serato DJ only scans the Auto Import folder at startup — it does not
watch for new files while running. So if Serato is active when we
download new tracks, they won't appear until the user restarts Serato.

We detect this and hold files in staging until Serato exits, then
flush them all to Auto Import at once.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Known Serato DJ process names on macOS (exact match via pgrep -x)
_SERATO_PROCESSES = [
    "Serato DJ Pro",
    "Serato DJ Lite",
]


def is_serato_running() -> bool:
    """Check if Serato DJ Pro or Lite is currently running."""
    try:
        for proc_name in _SERATO_PROCESSES:
            result = subprocess.run(
                ["pgrep", "-x", proc_name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return True
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # If pgrep fails, assume Serato is not running
        return False


def flush_staging_to_import(staging_dir: Path, import_dir: Path) -> list[Path]:
    """Move all tagged files from staging to the Auto Import directory.

    Called when Serato is NOT running, so Serato will pick them up
    on next launch. Uses os.replace for atomic moves.

    Returns list of files that were moved.
    """
    moved: list[Path] = []
    if not staging_dir.exists():
        return moved

    for file in sorted(staging_dir.iterdir()):
        if file.is_file() and file.suffix.lower() in (".flac", ".mp3", ".wav", ".aif", ".aiff"):
            dest = import_dir / file.name
            try:
                file.replace(dest)
                moved.append(dest)
                logger.info("Flushed to Auto Import: %s", dest.name)
            except OSError as e:
                logger.error("Failed to move %s to Auto Import: %s", file.name, e)

    return moved
