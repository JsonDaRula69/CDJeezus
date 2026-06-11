"""Local library scanning and AcoustID fingerprint assignment.

Scans the user's primary DJ library (Serato or Rekordbox), catalogs all
audio files, and assigns AcoustID fingerprints using chromaprint (fpcalc).
This data is used for the library upscaling flow — finding higher-quality
versions of existing files on Soulseek.
"""

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LibraryTrack:
    """A track in the user's local DJ library."""
    filepath: Path
    artist: str = ""
    title: str = ""
    album: str = ""
    duration_s: float | None = None
    bitrate: int | None = None
    format: str = ""  # "flac", "mp3", "aiff", "wav", "aac"
    filesize_mb: float = 0.0
    fingerprint: str = ""
    isrc: str = ""
    needs_upscale: bool = False  # True if format is lossy (MP3 < 320, AAC)


def scan_serato_library() -> list[LibraryTrack]:
    """Scan Serato's Imported and Subcrates directories for audio files."""
    serato_dir = Path.home() / "Music" / "_Serato_"
    tracks: list[LibraryTrack] = []
    seen: set[str] = set()

    # Scan Imported, Subcrates, and root music directories
    audio_dirs = [
        serato_dir / "Imported",
        Path.home() / "Music" / "_Serato_" / "Auto Import",
    ]

    # Also check common music locations
    for music_sub in Path.home().glob("Music/*/"):
        if music_sub.is_dir() and music_sub.name not in {
            "_Serato_", "LibraryBackups"
        }:
            audio_dirs.append(music_sub)

    for audio_dir in audio_dirs:
        if not audio_dir.exists():
            continue
        for ext in ("*.flac", "*.mp3", "*.aiff", "*.aif", "*.wav", "*.aac", "*.m4a"):
            for f in audio_dir.rglob(ext):
                canonical = str(f.resolve())
                if canonical in seen:
                    continue
                seen.add(canonical)
                track = _track_from_file(f)
                if track:
                    tracks.append(track)

    logger.info("Scanned %d tracks from Serato library", len(tracks))
    return tracks


def scan_rekordbox_library() -> list[LibraryTrack]:
    """Scan Rekordbox database for track file paths.

    Uses pyrekordbox if available, falls back to file system scan.
    """
    tracks: list[LibraryTrack] = []
    seen: set[str] = set()

    try:
        import pyrekordbox
        db = pyrekordbox.RbMasterDatabase()
        for content in db.query_content():
            path = Path(content.Path) if content.Path else None
            if not path or not path.exists():
                continue
            canonical = str(path.resolve())
            if canonical in seen:
                continue
            seen.add(canonical)
            track = _track_from_file(path)
            if track:
                if hasattr(content, 'Artist') and content.Artist:
                    track.artist = content.Artist.Name if hasattr(content.Artist, 'Name') else str(content.Artist)
                if hasattr(content, 'Title') and content.Title:
                    track.title = content.Title
                tracks.append(track)
        logger.info("Scanned %d tracks from Rekordbox database", len(tracks))
        return tracks
    except ImportError:
        logger.debug("pyrekordbox not available, falling back to filesystem scan")
    except Exception as e:
        logger.warning("Rekordbox database scan failed: %s", e)

    # Fallback: scan common music directories
    for music_sub in Path.home().glob("Music/*/"):
        if music_sub.is_dir() and music_sub.name not in {
            "_Serato_", "LibraryBackups"
        }:
            for ext in ("*.flac", "*.mp3", "*.aiff", "*.aif", "*.wav", "*.aac", "*.m4a"):
                for f in music_sub.rglob(ext):
                    canonical = str(f.resolve())
                    if canonical in seen:
                        continue
                    seen.add(canonical)
                    track = _track_from_file(f)
                    if track:
                        tracks.append(track)

    logger.info("Scanned %d tracks from filesystem", len(tracks))
    return tracks


def _track_from_file(filepath: Path) -> LibraryTrack | None:
    """Create a LibraryTrack from a file, reading its metadata."""
    suffix = filepath.suffix.lower().lstrip(".")
    fmt_map = {"flac": "flac", "mp3": "mp3", "aiff": "aiff", "aif": "aiff",
               "wav": "wav", "aac": "aac", "m4a": "aac"}
    fmt = fmt_map.get(suffix, "")
    if not fmt:
        return None

    track = LibraryTrack(
        filepath=filepath,
        format=fmt,
        filesize_mb=filepath.stat().st_size / (1024 * 1024),
    )

    # Read metadata
    try:
        from .metadata import verify_metadata
        meta = verify_metadata(filepath)
        track.artist = meta.get("artist", "")
        track.title = meta.get("title", "")
        track.album = meta.get("album", "")
        track.isrc = meta.get("isrc", "")
    except Exception:
        pass

    # Mark files that need upscaling
    if fmt == "mp3":
        track.needs_upscale = True  # Could check bitrate, but any MP3 < lossless
    elif fmt == "aac":
        track.needs_upscale = True

    return track


def fingerprint_library_tracks(tracks: list[LibraryTrack]) -> int:
    """Assign AcoustID fingerprints to library tracks using fpcalc.

    Returns the number of tracks successfully fingerprinted.
    """
    from .fingerprint import check_fpcalc, generate_fingerprint

    if not check_fpcalc():
        logger.warning("fpcalc not installed; skipping library fingerprinting")
        return 0

    count = 0
    for track in tracks:
        if track.fingerprint:
            continue
        result = generate_fingerprint(track.filepath)
        if result:
            track.fingerprint = result["fingerprint"]
            if result["duration"] and not track.duration_s:
                track.duration_s = result["duration"]
            count += 1
            if count % 50 == 0:
                logger.info("Fingerprinted %d/%d library tracks", count, len(tracks))

    logger.info("Fingerprinted %d library tracks (%d total)", count, len(tracks))
    return count
