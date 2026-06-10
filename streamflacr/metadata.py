"""Audio metadata tagging, verification, and enrichment via mutagen.

Writes artist, title, comment (set to the SoundCloud playlist name),
and other standard fields. The comment field is used because Serato
reliably reads it during import and it's visible in the library view.
The label field is also set for smart crate matching.

Supports both FLAC (Vorbis comments) and MP3 (ID3v2 tags).
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
    """Write core metadata to a FLAC or MP3 file.

    The 'comment' field is set to the SoundCloud playlist name so it's
    visible in Serato's library and reliably scanned on import.
    The 'label' field is also set for smart crate matching.
    """
    if filepath.suffix.lower() == ".flac":
        _tag_flac(filepath, artist, title, playlist_name, album, genre, year)
    elif filepath.suffix.lower() == ".mp3":
        _tag_mp3(filepath, artist, title, playlist_name, album, genre, year)
    else:
        logger.warning("Unsupported format for tagging: %s", filepath.suffix)
        return

    logger.info(
        "Tagged %s: artist=%s, title=%s, comment=%s",
        filepath.name, artist, title, playlist_name,
    )


def verify_metadata(filepath: Path) -> dict:
    """Read existing metadata from a file. Returns a dict of present fields."""
    result = {}
    try:
        if filepath.suffix.lower() == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(filepath))
            for key in ("artist", "title", "album", "genre", "date", "label",
                        "comment", "isrc", "composer", "publisher"):
                if key in audio:
                    result[key] = audio[key][0] if isinstance(audio[key], list) else audio[key]
        elif filepath.suffix.lower() == ".mp3":
            from mutagen.id3 import ID3
            audio = ID3(str(filepath))
            mapping = {
                "artist": "TPE1", "title": "TIT2", "album": "TALB",
                "genre": "TCON", "date": "TDRC", "publisher": "TPUB",
            }
            for key, frame_id in mapping.items():
                if frame_id in audio:
                    result[key] = str(audio[frame_id])
            # Comment is in COMM frames
            for frame in audio.getall("COMM"):
                if frame.desc == "" or frame.desc == "StreamFLACr":
                    result["comment"] = str(frame.text[0]) if frame.text else ""
                    break
    except Exception as e:
        logger.debug("Could not read metadata from %s: %s", filepath.name, e)
    return result


def enrich_metadata(
    filepath: Path,
    sc_track: "TrackInfo",
    playlist_name: str,
) -> None:
    """Cross-check downloaded file's metadata against SoundCloud data and fill gaps.

    The comment field is always overwritten with the playlist name since
    that's our primary mechanism for Serato smart crates. The label field
    is also set. Other fields are filled only if missing from the file.
    """
    existing = verify_metadata(filepath)

    # Determine what needs updating
    updates: dict = {}

    # Artist/title: always set from SoundCloud (canonical source)
    sc_artist = sc_track.canonical_artist or sc_track.artist
    existing_artist = existing.get("artist", "")
    if existing_artist and existing_artist.lower() != sc_artist.lower():
        logger.info(
            "Correcting artist: '%s' -> '%s' (from SoundCloud)",
            existing_artist, sc_artist,
        )

    # Album: fill from SoundCloud if missing in file
    if not existing.get("album") and sc_track.album:
        updates["album"] = sc_track.album
        logger.info("Filling missing album: '%s'", sc_track.album)

    # Genre: fill from SoundCloud if missing
    if not existing.get("genre") and sc_track.genre:
        updates["genre"] = sc_track.genre
        logger.info("Filling missing genre: '%s'", sc_track.genre)

    # ISRC: fill from SoundCloud if missing
    if not existing.get("isrc") and sc_track.isrc:
        updates["isrc"] = sc_track.isrc
        logger.info("Filling missing ISRC: '%s'", sc_track.isrc)

    # Composer: fill from SoundCloud publisher_metadata.writer_composer
    if not existing.get("composer") and sc_track.writer_composer:
        updates["composer"] = sc_track.writer_composer
        logger.info("Filling missing composer: '%s'", sc_track.writer_composer)

    # Apply updates
    if not updates:
        logger.debug("Metadata already complete for %s", filepath.name)
        return

    if filepath.suffix.lower() == ".flac":
        _enrich_flac(filepath, updates)
    elif filepath.suffix.lower() == ".mp3":
        _enrich_mp3(filepath, updates)

    logger.info("Enriched metadata for %s: %s", filepath.name, list(updates.keys()))


def _tag_flac(filepath, artist, title, playlist_name, album, genre, year):
    from mutagen.flac import FLAC
    audio = FLAC(str(filepath))
    audio["artist"] = artist
    audio["title"] = title
    audio["comment"] = playlist_name
    audio["label"] = playlist_name
    if album:
        audio["album"] = album
    if genre:
        audio["genre"] = genre
    if year:
        audio["date"] = year
    audio.save()


def _tag_mp3(filepath, artist, title, playlist_name, album, genre, year):
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCON, TDRC, TPUB, COMM
    audio = ID3(str(filepath))
    audio.add(TPE1(encoding=3, text=artist))
    audio.add(TIT2(encoding=3, text=title))
    # COMM with description "StreamFLACr" so we can identify our own comments
    audio.add(COMM(encoding=3, lang="eng", desc="StreamFLACr", text=playlist_name))
    audio.add(TPUB(encoding=3, text=playlist_name))
    if album:
        audio.add(TALB(encoding=3, text=album))
    if genre:
        audio.add(TCON(encoding=3, text=genre))
    if year:
        audio.add(TDRC(encoding=3, text=year))
    audio.save()


def _enrich_flac(filepath: Path, updates: dict):
    from mutagen.flac import FLAC
    audio = FLAC(str(filepath))
    for key, val in updates.items():
        audio[key] = val
    audio.save()


def _enrich_mp3(filepath: Path, updates: dict):
    from mutagen.id3 import ID3, TALB, TCON, TCOM, TDRC, TIPL, COMM
    audio = ID3(str(filepath))
    frame_map = {
        "album": lambda v: TALB(encoding=3, text=v),
        "genre": lambda v: TCON(encoding=3, text=v),
        "composer": lambda v: TCOM(encoding=3, text=v),
        "date": lambda v: TDRC(encoding=3, text=v),
        "isrc": lambda v: TIPL(encoding=3, text=[f"ISRC: {v}"]),
    }
    for key, val in updates.items():
        if key in frame_map:
            audio.add(frame_map[key](val))
    audio.save()
