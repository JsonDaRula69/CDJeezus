"""SoundCloud playlist discovery and monitoring.

Uses the SoundCloud API v2 with OAuth (extracted from Chrome cookies).
Requires the user to be logged into SoundCloud in Chrome — we prompt
for that during setup if the token isn't found.

Never triggers DRM protection because we only read metadata.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_cached_client_id: str | None = None
_cached_cookies: dict | None = None
_cached_oauth_token: str | None = None


@dataclass(frozen=True, slots=True)
class TrackInfo:
    """Track metadata from SoundCloud."""
    track_id: str
    title: str
    artist: str
    url: str
    duration_s: float | None = None
    genre: str | None = None
    album: str | None = None
    canonical_artist: str | None = None  # from publisher_metadata.artist
    writer_composer: str | None = None
    isrc: str | None = None


@dataclass
class PlaylistInfo:
    """A SoundCloud playlist/set with its tracks."""
    playlist_id: str
    title: str
    url: str
    tracks: list[TrackInfo] = field(default_factory=list)


# ── Chrome cookie decryption ────────────────────────────────────────────

def _get_client_id() -> str:
    """Extract the SoundCloud client_id from the homepage JS."""
    global _cached_client_id
    if _cached_client_id:
        return _cached_client_id
    resp = requests.get("https://soundcloud.com/", timeout=15)
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', resp.text)
    for script_url in reversed(scripts):
        script_resp = requests.get(script_url, timeout=15)
        match = re.search(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"', script_resp.text)
        if match:
            _cached_client_id = match.group(1)
            return _cached_client_id
    raise RuntimeError("Could not extract SoundCloud client_id")


def _decrypt_chrome_cookies() -> dict:
    """Decrypt SoundCloud cookies from Chrome's SQLite database."""
    global _cached_cookies
    if _cached_cookies is not None:
        return _cached_cookies

    import sqlite3
    import subprocess

    from Crypto.Cipher import AES
    from Crypto.Hash import SHA1, HMAC
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Util.Padding import unpad

    chrome_dir = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    cookie_db = None
    for profile in ("Default", "Profile 1", "Profile 2", "Profile 3"):
        candidate = chrome_dir / profile / "Cookies"
        if candidate.exists():
            cookie_db = candidate
            break

    if not cookie_db:
        _cached_cookies = {}
        return _cached_cookies

    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Chrome Safe Storage", "-a", "Chrome", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            _cached_cookies = {}
            return _cached_cookies
        safe_key = result.stdout.strip()
    except Exception:
        _cached_cookies = {}
        return _cached_cookies

    key = PBKDF2(
        safe_key.encode("utf-8"),
        b"saltysalt",
        dkLen=16,
        count=1003,
        prf=lambda p, s: HMAC.new(p, s, SHA1).digest(),
    )

    try:
        conn = sqlite3.connect(str(cookie_db))
        cur = conn.execute(
            "SELECT name, host_key, encrypted_value FROM cookies WHERE host_key LIKE '%soundcloud%'"
        )
        cookies = {}
        for name, host, enc_val in cur:
            if enc_val[:3] != b"v10":
                continue
            encrypted = enc_val[3:]
            cipher = AES.new(key, AES.MODE_CBC, b" " * 16)
            decrypted = cipher.decrypt(encrypted)
            try:
                unpadded = unpad(decrypted, AES.block_size)
            except ValueError:
                unpadded = decrypted
            text = unpadded.decode("utf-8", errors="replace")
            clean = re.sub(r"[^\x20-\x7e]", "", text)
            if clean and len(clean) < 500:
                cookies[name] = clean
        conn.close()
    except Exception as e:
        logger.debug("Chrome cookie extraction failed: %s", e)
        _cached_cookies = {}
        return _cached_cookies

    _cached_cookies = cookies
    return _cached_cookies


def _get_oauth_token() -> str | None:
    """Extract the OAuth token from Chrome cookies."""
    global _cached_oauth_token
    if _cached_oauth_token is not None:
        return _cached_oauth_token or None
    cookies = _decrypt_chrome_cookies()
    raw = cookies.get("oauth_token", "")
    if not raw:
        _cached_oauth_token = ""
        return None
    match = re.search(r"(\d+-\d+-\d+-[A-Za-z0-9]+)", raw)
    _cached_oauth_token = match.group(1) if match else ""
    return _cached_oauth_token or None


# ── API requests ─────────────────────────────────────────────────────────

def _api_get(endpoint: str, params: dict | None = None) -> dict | None:
    """SoundCloud API v2 request with dual-attempt authentication.

    Sending client_id + OAuth together causes 403 on some endpoints,
    so we try OAuth first, then client_id-only as fallback.
    """
    params = dict(params or {})
    base_url = f"https://api-v2.soundcloud.com/{endpoint}"
    token = _get_oauth_token()

    # Attempt 1: OAuth header (no client_id)
    if token:
        headers = {"Authorization": f"OAuth {token}"}
        try:
            resp = requests.get(base_url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("OAuth attempt on %s returned %d", endpoint, resp.status_code)
        except requests.RequestException as e:
            logger.debug("OAuth request failed on %s: %s", endpoint, e)

    # Attempt 2: client_id only (no auth)
    client_id = _get_client_id()
    params["client_id"] = client_id
    try:
        resp = requests.get(base_url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        logger.debug("client_id attempt on %s returned %d", endpoint, resp.status_code)
    except requests.RequestException as e:
        logger.debug("client_id request failed on %s: %s", endpoint, e)

    return None


def has_oauth() -> bool:
    """Check whether an OAuth token was found in Chrome cookies."""
    return _get_oauth_token() is not None


# ── Track / playlist data extraction ────────────────────────────────────

def _track_from_api(track_data: dict) -> TrackInfo:
    """Build a TrackInfo from a SoundCloud API track object."""
    title = track_data.get("title", "")
    artist = track_data.get("user", {}).get("username", "")
    track_id = str(track_data.get("id", ""))
    permalink = track_data.get("permalink_url", "")
    duration_ms = track_data.get("duration", 0)
    duration_s = duration_ms / 1000 if duration_ms else None
    genre = track_data.get("genre") or None
    pm = track_data.get("publisher_metadata", {})
    album = pm.get("album_title") if pm else None
    canonical_artist = pm.get("artist") if pm else None
    writer_composer = pm.get("writer_composer") if pm else None
    isrc = pm.get("isrc") if pm else None

    return TrackInfo(
        track_id=track_id,
        title=title,
        artist=artist,
        url=permalink,
        duration_s=duration_s,
        genre=genre,
        album=album,
        canonical_artist=canonical_artist,
        writer_composer=writer_composer,
        isrc=isrc,
    )


# ── Playlist discovery ──────────────────────────────────────────────────

def _get_user_id() -> int | None:
    """Get the authenticated user's SoundCloud ID via /me."""
    data = _api_get("me")
    if data:
        return data.get("id")
    return None


def discover_user_playlists(user_sets_url: str | None = None) -> list[PlaylistInfo]:
    """Discover all playlists for the authenticated user via API v2."""
    user_id = _get_user_id()
    if not user_id:
        logger.error("Could not identify SoundCloud user. Is Chrome logged into SoundCloud?")
        return []

    data = _api_get(f"users/{user_id}/playlists", {"limit": 50, "representation": "full"})
    if not data:
        logger.error("Failed to fetch playlists for user %d", user_id)
        return []

    playlists_raw: list[dict]
    if isinstance(data, list):
        playlists_raw = data
    elif isinstance(data, dict):
        playlists_raw = data.get("collection", data.get("playlists", []))
    else:
        return []

    results: list[PlaylistInfo] = []
    for p in playlists_raw:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id", ""))
        title = p.get("title", "?")
        permalink = p.get("permalink_url", "")
        if pid and permalink:
            tracks: list[TrackInfo] = []
            raw_tracks = p.get("tracks", [])
            if isinstance(raw_tracks, list):
                tracks = [_track_from_api(t) for t in raw_tracks if isinstance(t, dict)]
            results.append(PlaylistInfo(playlist_id=pid, title=title, url=permalink, tracks=tracks))

    logger.info("Discovered %d playlists for user %d", len(results), user_id)
    return results


# ── Playlist track fetching ──────────────────────────────────────────────

def fetch_playlist_tracks(playlist_url: str) -> list[TrackInfo]:
    """Fetch all tracks from a SoundCloud playlist via API v2."""
    data = _api_get("resolve", {"url": playlist_url})
    if data and isinstance(data.get("tracks"), list) and data["tracks"]:
        tracks = [_track_from_api(t) for t in data["tracks"]]
        logger.info("API returned %d tracks for playlist '%s'", len(tracks), data.get("title", ""))
        return tracks

    # If resolve didn't include full tracks, try the playlist ID directly
    if data and data.get("id"):
        pid = data["id"]
        full = _api_get(f"playlists/{pid}", {"representation": "full"})
        if full and isinstance(full.get("tracks"), list) and full["tracks"]:
            tracks = [_track_from_api(t) for t in full["tracks"]]
            logger.info("Direct playlist API returned %d tracks for '%s'", len(tracks), full.get("title", ""))
            return tracks

    logger.warning("Could not fetch tracks for playlist: %s", playlist_url)
    return []


def refresh_playlist_tracks(playlist: PlaylistInfo) -> PlaylistInfo:
    """Re-fetch tracks for a playlist."""
    tracks = fetch_playlist_tracks(playlist.url)
    playlist.tracks = tracks
    return playlist
