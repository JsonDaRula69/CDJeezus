"""Audio metadata tagging via mutagen.

Writes artist, title, label (set to the SoundCloud playlist name),
and other standard fields. Supports both FLAC (Vorbis comments) and
MP3 (ID3v2 tags).
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def tag_file(
    filepath: Path,
    artist: str,
    title: str,
    playlist_name: str,
    album: str | None = None,
    genre: str | None = None,
    year: str | None = None,
) -> None:
    """Write metadata to a FLAC or MP3 file.

    The 'label' field is set to the SoundCloud playlist name so
    Serato smart crates can match on it.
    """
    if filepath.suffix.lower() == ".flac":
        _tag_flac(filepath, artist, title, playlist_name, album, genre, year)
    elif filepath.suffix.lower() == ".mp3":
        _tag_mp3(filepath, artist, title, playlist_name, album, genre, year)
    else:
        logger.warning("Unsupported format for tagging: %s", filepath.suffix)
        return

    logger.info(
        "Tagged %s: artist=%s, title=%s, label=%s",
        filepath.name, artist, title, playlist_name,
    )


def _tag_flac(
    filepath: Path,
    artist: str,
    title: str,
    playlist_name: str,
    album: str | None,
    genre: str | None,
    year: str | None,
) -> None:
    from mutagen.flac import FLAC

    audio = FLAC(str(filepath))
    audio["artist"] = artist
    audio["title"] = title
    audio["label"] = playlist_name
    if album:
        audio["album"] = album
    if genre:
        audio["genre"] = genre
    if year:
        audio["date"] = year
    audio.save()


def _tag_mp3(
    filepath: Path,
    artist: str,
    title: str,
    playlist_name: str,
    album: str | None,
    genre: str | None,
    year: str | None,
) -> None:
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCON, TDRC, TPUB

    audio = ID3(str(filepath))
    audio.add(TPE1(encoding=3, text=artist))       # Artist
    audio.add(TIT2(encoding=3, text=title))         # Title
    audio.add(TPUB(encoding=3, text=playlist_name))  # Label / Publisher
    if album:
        audio.add(TALB(encoding=3, text=album))
    if genre:
        audio.add(TCON(encoding=3, text=genre))
    if year:
        audio.add(TDRC(encoding=3, text=year))
    audio.save()
