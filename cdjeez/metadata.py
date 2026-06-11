"""Audio metadata tagging, verification, and enrichment via mutagen.

Writes artist, title, description (set to the SoundCloud playlist name),
and other standard fields. The 'description' Vorbis tag / ID3 COMM with empty
description is what Serato DJ reads for its Comment column, which is matched
by smart crate rules (Comment IS <playlist_name>).

Supports FLAC (Vorbis comments), MP3 (ID3v2 tags), and AIFF (ID3v2 tags).
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
    label_name: str | None = None,
) -> None:
    """Write core metadata to a FLAC or MP3 file.

    The 'description' Vorbis tag (FLAC) or COMM with empty description (MP3)
    is set to the playlist name for Serato smart crate matching.
    The 'label' field is set to the SoundCloud label_name (record company).
    """
    if filepath.suffix.lower() == ".flac":
        _tag_flac(filepath, artist, title, playlist_name, album, genre, year, label_name)
    elif filepath.suffix.lower() == ".mp3":
        _tag_mp3(filepath, artist, title, playlist_name, album, genre, year, label_name)
    elif filepath.suffix.lower() in (".aiff", ".aif"):
        _tag_aiff(filepath, artist, title, playlist_name, album, genre, year, label_name)
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
                        "comment", "description", "isrc", "composer", "publisher"):
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
            for frame in audio.getall("COMM"):
                if frame.desc == "CDJeez" or frame.desc == "":
                    result["comment"] = str(frame.text[0]) if frame.text else ""
                    break
        elif filepath.suffix.lower() in (".aiff", ".aif"):
            from mutagen.aiff import AIFF
            audio = AIFF(str(filepath))
            if audio.tags:
                mapping = {
                    "artist": "TPE1", "title": "TIT2", "album": "TALB",
                    "genre": "TCON", "date": "TDRC", "publisher": "TPUB",
                }
                for key, frame_id in mapping.items():
                    if frame_id in audio.tags:
                        result[key] = str(audio.tags[frame_id])
                for frame in audio.tags.getall("COMM"):
                    if frame.desc == "" or frame.desc == "CDJeez":
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
    that's our primary mechanism for Serato smart crate matching.
    Other fields are filled only if missing from the downloaded file.
    """
    existing = verify_metadata(filepath)

    updates: dict = {}

    # Artist/title: always set from SoundCloud (canonical source)
    sc_artist = sc_track.canonical_artist or sc_track.artist
    existing_artist = existing.get("artist", "")
    if existing_artist and existing_artist.lower() != sc_artist.lower():
        logger.info(
            "Correcting artist: '%s' -> '%s' (from SoundCloud)",
            existing_artist, sc_artist,
        )

    # Album: fill from SoundCloud if missing
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

    # Label: set from SoundCloud label_name (record company)
    if sc_track.label_name:
        if not existing.get("label"):
            updates["label"] = sc_track.label_name
            logger.info("Filling missing label: '%s'", sc_track.label_name)
        elif existing.get("label", "").lower() != sc_track.label_name.lower():
            # Overwrite with SoundCloud's canonical label name
            updates["label"] = sc_track.label_name
            logger.info("Overwriting label: '%s' -> '%s'", existing.get("label"), sc_track.label_name)

    if not updates:
        logger.debug("Metadata already complete for %s", filepath.name)
        return

    if filepath.suffix.lower() == ".flac":
        _enrich_flac(filepath, updates)
    elif filepath.suffix.lower() == ".mp3":
        _enrich_mp3(filepath, updates)
    elif filepath.suffix.lower() in (".aiff", ".aif"):
        _enrich_aiff(filepath, updates)

    logger.info("Enriched metadata for %s: %s", filepath.name, list(updates.keys()))


def _tag_flac(filepath, artist, title, playlist_name, album, genre, year, label_name=None):
    from mutagen.flac import FLAC
    audio = FLAC(str(filepath))
    audio["artist"] = artist
    audio["title"] = title
    # Serato DJ reads the 'description' Vorbis tag for its Comment column.
    audio["description"] = playlist_name
    if label_name:
        audio["label"] = label_name
    if album:
        audio["album"] = album
    if genre:
        audio["genre"] = genre
    if year:
        audio["date"] = year
    audio.save()


def _tag_mp3(filepath, artist, title, playlist_name, album, genre, year, label_name=None):
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCON, TDRC, COMM, TPUB
    audio = ID3(str(filepath))
    audio.add(TPE1(encoding=3, text=artist))
    audio.add(TIT2(encoding=3, text=title))
    # Serato DJ reads COMM with empty description for its Comment column.
    audio.add(COMM(encoding=3, lang="eng", desc="", text=playlist_name))
    if label_name:
        audio.add(TPUB(encoding=3, text=label_name))
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
    # Keep description in sync with comment if comment is being updated
    if "comment" in updates:
        audio["description"] = updates["comment"]
    audio.save()


def _enrich_mp3(filepath: Path, updates: dict):
    from mutagen.id3 import ID3, TALB, TCON, TCOM, TDRC, TIPL, TPUB
    audio = ID3(str(filepath))
    frame_map = {
        "album": lambda v: TALB(encoding=3, text=v),
        "genre": lambda v: TCON(encoding=3, text=v),
        "composer": lambda v: TCOM(encoding=3, text=v),
        "date": lambda v: TDRC(encoding=3, text=v),
        "isrc": lambda v: TIPL(encoding=3, text=[f"ISRC: {v}"]),
        "label": lambda v: TPUB(encoding=3, text=v),
    }
    for key, val in updates.items():
        if key in frame_map:
            audio.add(frame_map[key](val))
    audio.save()


def _tag_aiff(filepath, artist, title, playlist_name, album, genre, year, label_name=None):
    """Tag an AIFF file using ID3v2 tags (same as MP3 but via mutagen.aiff)."""
    from mutagen.aiff import AIFF
    from mutagen.id3 import TIT2, TPE1, TALB, TCON, TDRC, COMM, TPUB
    try:
        audio = AIFF(str(filepath))
    except Exception:
        audio = AIFF()
    if audio.tags is None:
        audio.add_tags()
    audio.tags.add(TPE1(encoding=3, text=artist))
    audio.tags.add(TIT2(encoding=3, text=title))
    # Serato reads COMM with empty description for Comment column
    audio.tags.add(COMM(encoding=3, lang="eng", desc="", text=playlist_name))
    if label_name:
        audio.tags.add(TPUB(encoding=3, text=label_name))
    if album:
        audio.tags.add(TALB(encoding=3, text=album))
    if genre:
        audio.tags.add(TCON(encoding=3, text=genre))
    if year:
        audio.tags.add(TDRC(encoding=3, text=year))
    audio.save()


def _enrich_aiff(filepath: Path, updates: dict):
    """Enrich AIFF metadata (same ID3v2 frames as MP3)."""
    from mutagen.aiff import AIFF
    from mutagen.id3 import TALB, TCON, TCOM, TDRC, TIPL, TPUB
    try:
        audio = AIFF(str(filepath))
    except Exception:
        return
    if audio.tags is None:
        audio.add_tags()
    frame_map = {
        "album": lambda v: TALB(encoding=3, text=v),
        "genre": lambda v: TCON(encoding=3, text=v),
        "composer": lambda v: TCOM(encoding=3, text=v),
        "date": lambda v: TDRC(encoding=3, text=v),
        "isrc": lambda v: TIPL(encoding=3, text=[f"ISRC: {v}"]),
        "label": lambda v: TPUB(encoding=3, text=v),
    }
    for key, val in updates.items():
        if key in frame_map:
            audio.tags.add(frame_map[key](val))
    audio.save()
