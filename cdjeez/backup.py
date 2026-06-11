"""Library backup system for Serato and Rekordbox.

Zips the metadata portions of DJ libraries (excluding actual audio files)
and stores them in ~/Music/LibraryBackups. Backups run before/after every
CDJeez session and on exit of the selected DJ software(s).

Serato backup excludes: Auto Import, Imported, Recording, SeratoVideo
Rekordbox backup: database files + share directory (no audio files)
"""

import logging
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from .config import BACKUP_DIR, SERATO_DIR, REKORDBOX_DIR

logger = logging.getLogger(__name__)

MAX_BACKUPS = 10

# Directories to exclude from Serato backup (audio, video, and large caches)
SERATO_EXCLUDE_DIRS = {"Auto Import", "Imported", "Recording", "SeratoVideo"}
# Subdirectories to exclude (large cached data)
SERATO_EXCLUDE_SUBDIRS = {"Metadata/SoundCloud"}

# Rekordbox paths to include (metadata only, no audio)
REKORDBOX_INCLUDE_FILES = [
    "master.db", "master.backup.db", "master.backup2.db", "master.backup3.db",
    "masterPlaylists6.xml", "automixPlaylist6.xml",
    "datafile.edb", "datafile.backup.edb",
    "ExtData.edb", "ExtData.backup.edb",
    "networkAnalyze6.db", "networkRecommend.db", "product.db",
]


def _rotate_backups(backup_dir: Path) -> None:
    """Keep only the most recent MAX_BACKUPS backup files."""
    if not backup_dir.exists():
        return
    backups = sorted(
        [p for p in backup_dir.iterdir() if p.is_file() and p.suffix == ".zip"],
        key=lambda p: p.name,
    )
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        oldest.unlink()
        logger.debug("Removed old backup: %s", oldest.name)


def backup_serato(backup_dir: Path) -> Path | None:
    """Zip the Serato metadata folder (excluding audio/video dirs).

    Returns the path to the created zip, or None if nothing to back up.
    """
    if not SERATO_DIR.exists():
        logger.debug("Serato directory not found, skipping backup")
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = backup_dir / f"serato_{timestamp}.zip"

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in SERATO_DIR.iterdir():
            if item.is_dir() and item.name in SERATO_EXCLUDE_DIRS:
                continue
            if item.is_file():
                zf.write(item, f"_Serato_/{item.name}")
                count += 1
            elif item.is_dir():
                for f in item.rglob("*"):
                    if f.is_file():
                        rel = Path(item.name) / f.relative_to(item)
                        # Skip excluded subdirectories
                        if any(str(rel).startswith(exc) for exc in SERATO_EXCLUDE_SUBDIRS):
                            continue
                        arcname = f"_Serato_/{rel}"
                        zf.write(f, str(arcname))
                        count += 1

    if count == 0:
        zip_path.unlink(missing_ok=True)
        return None

    _rotate_backups(backup_dir)
    logger.info("Backed up Serato metadata (%d files) to %s", count, zip_path.name)
    return zip_path


def backup_rekordbox(backup_dir: Path) -> Path | None:
    """Zip Rekordbox database files (metadata only, no audio).

    Returns the path to the created zip, or None if nothing to back up.
    """
    if not REKORDBOX_DIR.exists():
        logger.debug("Rekordbox directory not found, skipping backup")
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = backup_dir / f"rekordbox_{timestamp}.zip"

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in REKORDBOX_DIR.iterdir():
            if item.is_file() and item.name in REKORDBOX_INCLUDE_FILES:
                zf.write(item, f"rekordbox/{item.name}")
                count += 1
        # Include share directory if it exists
        share_dir = REKORDBOX_DIR / "share"
        if share_dir.exists():
            for f in share_dir.rglob("*"):
                if f.is_file():
                    arcname = f"rekordbox/share/{f.relative_to(share_dir)}"
                    zf.write(f, arcname)
                    count += 1

    if count == 0:
        zip_path.unlink(missing_ok=True)
        return None

    _rotate_backups(backup_dir)
    logger.info("Backed up Rekordbox metadata (%d files) to %s", count, zip_path.name)
    return zip_path


def run_backups(backup_serato: bool = True, backup_rekordbox: bool = True) -> list[Path]:
    """Run configured library backups.

    Returns list of created backup zip paths.
    """
    results: list[Path] = []
    if backup_serato:
        path = backup_serato(BACKUP_DIR)
        if path:
            results.append(path)
    if backup_rekordbox:
        path = backup_rekordbox(BACKUP_DIR)
        if path:
            results.append(path)
    return results
