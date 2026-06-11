"""Audio format conversion via ffmpeg.

Converts FLAC and WAV files to AIFF for maximum Serato/CDJ compatibility.
AIFF is the preferred format because:
- Native support on all CDJ models
- Serato reads AIFF metadata reliably
- No quality loss (both are uncompressed PCM)
- ID3v2 tags work consistently in Serato's Comment column

Uses ffmpeg for conversion, preserving audio quality with -c:a pcm_s16be.
"""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Formats that need conversion to AIFF for Serato/CDJ compatibility
CONVERTIBLE_FORMATS = {".flac", ".wav", ".aif"}


def check_ffmpeg() -> bool:
    """Check if ffmpeg is available on the system."""
    return shutil.which("ffmpeg") is not None


def needs_conversion(filepath: Path) -> bool:
    """Check if a file needs to be converted to AIFF."""
    return filepath.suffix.lower() in CONVERTIBLE_FORMATS


def convert_to_aiff(
    source: Path,
    dest_dir: Path | None = None,
    delete_source: bool = True,
) -> Path | None:
    """Convert a FLAC or WAV file to AIFF using ffmpeg.

    Args:
        source: Path to the source audio file.
        dest_dir: Directory for the output AIFF. Defaults to source's parent.
        delete_source: Whether to delete the original after successful conversion.

    Returns:
        Path to the converted AIFF file, or None if conversion failed.
    """
    if not check_ffmpeg():
        logger.warning("ffmpeg not found — cannot convert %s to AIFF", source.name)
        return None

    if source.suffix.lower() == ".aiff":
        return source  # Already AIFF

    dest_dir = dest_dir or source.parent
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Build output path: same stem, .aiff extension
    stem = source.stem
    dest = dest_dir / f"{stem}.aiff"

    # Avoid overwriting if dest already exists
    if dest.exists() and dest != source:
        logger.debug("AIFF already exists: %s", dest.name)
        return dest

    cmd = [
        "ffmpeg",
        "-y",           # Overwrite output without asking
        "-i", str(source),
        "-c:a", "pcm_s16be",  # Big-endian 16-bit PCM (standard AIFF)
        dest.as_posix(),
    ]

    logger.info("Converting %s -> AIFF: %s", source.suffix.lstrip(".").upper(), source.name)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout for large files
        )
        if result.returncode != 0:
            logger.error("ffmpeg conversion failed: %s", result.stderr[-500:] if result.stderr else "unknown error")
            if dest.exists():
                dest.unlink(missing_ok=True)
            return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg conversion timed out for %s", source.name)
        if dest.exists():
            dest.unlink(missing_ok=True)
        return None
    except FileNotFoundError:
        logger.error("ffmpeg not found during conversion")
        return None

    if not dest.exists() or dest.stat().st_size == 0:
        logger.error("Conversion produced empty/missing file: %s", dest.name)
        return None

    logger.info("Converted to AIFF: %s (%.1f MB)",
                dest.name, dest.stat().st_size / (1024 * 1024))

    if delete_source and source != dest:
        try:
            source.unlink()
            logger.debug("Deleted source: %s", source.name)
        except OSError as e:
            logger.warning("Could not delete source %s: %s", source.name, e)

    return dest
