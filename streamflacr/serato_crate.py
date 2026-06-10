"""Serato smart crate management via serato-tools.

Creates a smart crate per SoundCloud playlist with a single rule:
    Comment IS <playlist_name>
The comment field is reliably written by StreamFLACr and consistently
read by Serato during Auto Import scanning.

Serato data is highly sensitive — we back up before any modification
and never delete existing crates or files.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

from .config import SERATO_DIR

logger = logging.getLogger(__name__)

BACKUP_DIR = Path.home() / "Music" / "_Serato_Backup_SFr"
MAX_BACKUPS = 5


def _rotate_backups() -> None:
    """Keep only the most recent MAX_BACKUPS backup directories."""
    if not BACKUP_DIR.exists():
        return
    backups = sorted(
        [p for p in BACKUP_DIR.iterdir() if p.is_dir() and p.name.startswith("Bk")],
        key=lambda p: p.name,
    )
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        shutil.rmtree(oldest)
        logger.debug("Removed old backup: %s", oldest.name)


def backup_serato_changes(*paths: Path) -> None:
    """Back up Serato files before modifying them.

    Creates a timestamped backup directory and copies the given files
    into it, preserving directory structure relative to SERATO_DIR.
    Only backs up files that actually exist.
    """
    existing = [p for p in paths if p.exists()]
    if not existing:
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dest = BACKUP_DIR / f"Bk{timestamp}"
    backup_dest.mkdir(parents=True, exist_ok=True)

    for path in existing:
        rel = path.relative_to(SERATO_DIR) if path.is_relative_to(SERATO_DIR) else path.name
        dest = backup_dest / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    _rotate_backups()
    logger.info("Backed up %d file(s) to %s", len(existing), backup_dest.name)


def _ensure_serato_tools():
    """Import serato_tools.smart_crate, auto-installing with --no-deps if missing.

    serato-tools depends on librosa (which pulls in numba/llvmlite that fails to
    build), but we only use smart_crate.py which needs none of that.
    """
    try:
        from serato_tools.smart_crate import SmartCrate
        return SmartCrate
    except ImportError:
        pass

    import subprocess
    import sys

    logger.info("Installing serato-tools (SmartCrate support)...")
    python = sys.executable

    # Try uv first (faster), then pip
    for cmd in (
        ["uv", "pip", "install", "--python", python, "serato-tools", "--no-deps"],
        [python, "-m", "pip", "install", "serato-tools", "--no-deps"],
    ):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info("serato-tools installed successfully")
            break
    else:
        logger.error("Could not install serato-tools; smart crate creation will be skipped")
        logger.error("Install manually: %s -m pip install serato-tools --no-deps", python)
        return None

    try:
        from serato_tools.smart_crate import SmartCrate
        return SmartCrate
    except ImportError:
        logger.error("serato-tools import failed after install")
        return None


def ensure_smart_crate(playlist_name: str) -> Path | None:
    """Create or update a Serato smart crate with Comment IS <playlist_name>.

    The comment field is reliably written by StreamFLACr and consistently
    read by Serato during Auto Import scanning.

    Backs up the file before any modification. Never deletes existing crates.
    Returns the path to the .scrate file, or None if serato-tools can't be installed.
    """
    SmartCrate = _ensure_serato_tools()
    if SmartCrate is None:
        return None

    safe_name = playlist_name.replace("/", "≫").replace("\\", "≫")
    smart_crates_dir = SERATO_DIR / "SmartCrates"
    smart_crates_dir.mkdir(parents=True, exist_ok=True)
    scrate_path = smart_crates_dir / f"{safe_name}.scrate"

    if scrate_path.exists():
        # Back up before overwriting
        backup_serato_changes(scrate_path)
        logger.info("Smart crate already exists: %s", scrate_path.name)
        sc = SmartCrate(str(scrate_path))
        _ensure_comment_rule(sc, playlist_name)
        sc.save()
        return scrate_path

    sc = SmartCrate(str(scrate_path))
    _ensure_comment_rule(sc, playlist_name)

    # Enable live update and match-all so Serato refreshes automatically
    for i, (f, v) in enumerate(sc.entries):
        if f == SmartCrate.Fields.SMARTCRATE_LIVE_UPDATE:
            sc.entries[i] = (f, [("brut", True)])
        if f == SmartCrate.Fields.SMARTCRATE_MATCH_ALL:
            sc.entries[i] = (f, [("brut", True)])

    sc.save()
    logger.info("Created smart crate: %s (Comment IS '%s')", scrate_path.name, playlist_name)
    return scrate_path


def _ensure_comment_rule(sc, playlist_name: str) -> None:
    """Set the smart crate rule to Comment IS <playlist_name>."""
    sc.set_rule(sc.RuleField.COMMENT, sc.RuleComparison.STR_IS, playlist_name)
