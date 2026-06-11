"""Persistent state tracking to avoid re-downloading tracks."""

import json
import logging
from pathlib import Path

from .config import STATE_FILE

logger = logging.getLogger(__name__)

# Current state schema version — bump when structure changes
STATE_VERSION = 5


class StateManager:
    """Tracks which tracks have been seen and downloaded.

    State schema (v4):
    {
        "version": 4,
        "playlists": {
            "<playlist_url>": {
                "name": "<playlist_name>",
                "seen_track_ids": ["12345", "67890", ...],
                "downloaded": {
                    "12345": {
                        "artist": "...",
                        "title": "...",
                        "local_path": "...",
                        "downloaded_at": "2026-01-01T00:00:00",
                        "verified": true,
                        "verification_method": "isrc_match",
                        "verification_confidence": 1.0
                    }
                }
            }
        },
        "serato_blocked_transfer": false,
        "library_fingerprinted": false,
        "upscale_prompted": false
    }
    """

    def __init__(self, state_file: Path | None = None):
        self.state_file = state_file or STATE_FILE
        self._state: dict = {"version": STATE_VERSION, "playlists": {}, "serato_blocked_transfer": False}
        self.load()

    def load(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                # Migrate older state formats
                version = data.get("version", 1)
                if version < STATE_VERSION:
                    data = self._migrate(data, version)
                self._state = data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not load state file: %s", e)
                self._state = {"version": STATE_VERSION, "playlists": {}, "serato_blocked_transfer": False}

    def save(self) -> None:
        self._state["version"] = STATE_VERSION
        self.state_file.write_text(json.dumps(self._state, indent=2))

    def _migrate(self, data: dict, from_version: int) -> dict:
        """Migrate state from an older schema version."""
        if from_version < 2:
            # v1 -> v2: Ensure downloaded entries have all expected fields
            for url, playlist in data.get("playlists", {}).items():
                for tid, info in playlist.get("downloaded", {}).items():
                    info.setdefault("local_path", "")
                    info.setdefault("downloaded_at", "")
        if from_version < 3:
            # v2 -> v3: Add serato_blocked_transfer flag
            data.setdefault("serato_blocked_transfer", False)
        if from_version < 4:
            # v3 -> v4: Add verification fields to downloaded entries
            for url, playlist in data.get("playlists", {}).items():
                for tid, info in playlist.get("downloaded", {}).items():
                    info.setdefault("verified", None)
                    info.setdefault("verification_method", "")
                    info.setdefault("verification_confidence", 0.0)
            logger.info("Migrated state from v3 to v4")
        if from_version < 5:
            # v4 -> v5: Add library fingerprinting and upscale tracking
            data.setdefault("library_fingerprinted", False)
            data.setdefault("upscale_prompted", False)
            logger.info("Migrated state from v4 to v5")
        data["version"] = STATE_VERSION
        return data

    def get_seen_ids(self, playlist_url: str) -> set[str]:
        playlist = self._state["playlists"].get(playlist_url, {})
        return set(playlist.get("seen_track_ids", []))

    def mark_seen(self, playlist_url: str, track_ids: list[str]) -> None:
        if playlist_url not in self._state["playlists"]:
            self._state["playlists"][playlist_url] = {"seen_track_ids": [], "downloaded": {}}
        existing = set(self._state["playlists"][playlist_url].get("seen_track_ids", []))
        existing.update(track_ids)
        self._state["playlists"][playlist_url]["seen_track_ids"] = list(existing)

    def mark_downloaded(self, playlist_url: str, track_id: str, artist: str, title: str, local_path: str, verified: bool | None = None, verification_method: str = "", verification_confidence: float = 0.0) -> None:
        if playlist_url not in self._state["playlists"]:
            self._state["playlists"][playlist_url] = {"seen_track_ids": [], "downloaded": {}}
        from datetime import datetime, timezone
        self._state["playlists"][playlist_url]["downloaded"][track_id] = {
            "artist": artist,
            "title": title,
            "local_path": local_path,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "verified": verified,
            "verification_method": verification_method,
            "verification_confidence": verification_confidence,
        }
        self.save()

    def set_playlist_name(self, playlist_url: str, name: str) -> None:
        if playlist_url not in self._state["playlists"]:
            self._state["playlists"][playlist_url] = {"seen_track_ids": [], "downloaded": {}}
        self._state["playlists"][playlist_url]["name"] = name

    @property
    def serato_blocked(self) -> bool:
        """Whether files are pending in staging because Serato is running."""
        return self._state.get("serato_blocked_transfer", False)

    @serato_blocked.setter
    def serato_blocked(self, value: bool) -> None:
        self._state["serato_blocked_transfer"] = value
        self.save()
