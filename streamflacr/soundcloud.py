"""SoundCloud playlist discovery and monitoring.

Uses yt-dlp with Chrome cookies to discover all user playlists (including
private ones), and the SoundCloud API v2 with decrypted Chrome cookies
to resolve playlist tracks and extract rich metadata (duration, genre,
publisher info).
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yt_dlp

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


def _get_client_id() -> str:
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
    global _cached_oauth_token
    if _cached_oauth_token is not None:
        return _cached_oauth_token
    cookies = _decrypt_chrome_cookies()
    raw = cookies.get("oauth_token", "")
    if not raw:
        _cached_oauth_token = ""
        return None
    match = re.search(r"(\d+-\d+-\d+-[A-Za-z0-9]+)", raw)
    _cached_oauth_token = match.group(1) if match else ""
    return _cached_oauth_token or None


def _api_session() -> requests.Session:
    session = requests.Session()
    cookies = _decrypt_chrome_cookies()
    for name, val in cookies.items():
        session.cookies.set(name, val, domain=".soundcloud.com")
    token = _get_oauth_token()
    if token:
        session.headers["Authorization"] = f"OAuth {token}"
    return session


def _api_get(endpoint: str, params: dict | None = None) -> dict | None:
    client_id = _get_client_id()
    params = dict(params or {})
    params["client_id"] = client_id
    session = _api_session()
    try:
        resp = session.get(f"https://api-v2.soundcloud.com/{endpoint}", params=params, timeout=15)
    except requests.RequestException as e:
        logger.debug("API request failed: %s", e)
        return None
    if resp.status_code == 200:
        return resp.json()
    logger.debug("SoundCloud API %s returned %d", endpoint, resp.status_code)
    return None


def _api_resolve(url: str) -> dict | None:
    return _api_get("resolve", {"url": url})


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


def discover_user_playlists(user_sets_url: str | None = None) -> list[PlaylistInfo]:
    if not user_sets_url:
        me = _api_get("me")
        if me:
            permalink = me.get("permalink_url", "")
            if permalink:
                user_sets_url = f"{permalink}/sets"
        if not user_sets_url:
            logger.error("Cannot determine SoundCloud user URL. Set SOUNDCLOUD_USER_URL in .env")
            return []

    logger.info("Discovering playlists from %s", user_sets_url)
    playlists = _yt_dlp_discover_playlists(user_sets_url)
    logger.info("Found %d playlists", len(playlists))
    return playlists


def _yt_dmp_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "cookiesfrombrowser": ("chrome",),
        "ignoreerrors": True,
    }


def _yt_dlp_discover_playlists(sets_url: str) -> list[PlaylistInfo]:
    ydl_opts = {**_yt_dmp_opts(), "extract_flat": True}
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            result = ydl.extract_info(sets_url, download=False)
        except yt_dlp.utils.ExtractorError as e:
            logger.error("yt-dlp failed to list playlists: %s", e)
            return results

    if not result or "entries" not in result:
        return results

    for entry in result["entries"]:
        if not entry:
            continue
        url = entry.get("url", "")
        title = entry.get("title", "?")
        playlist_id = str(entry.get("id", ""))
        if url:
            results.append(PlaylistInfo(playlist_id=playlist_id, title=title, url=url))

    return results


def fetch_playlist_tracks(playlist_url: str) -> list[TrackInfo]:
    """Fetch all tracks from a SoundCloud playlist with rich metadata."""
    data = _api_resolve(playlist_url)
    if data and "tracks" in data:
        tracks = [_track_from_api(t) for t in data["tracks"]]
        logger.info("API returned %d tracks for playlist '%s'", len(tracks), data.get("title", ""))
        return tracks

    logger.info("Falling back to yt-dlp for playlist: %s", playlist_url)
    return _yt_dlp_playlist_tracks(playlist_url)


def _yt_dlp_playlist_tracks(playlist_url: str) -> list[TrackInfo]:
    ydl_opts = {**_yt_dmp_opts(), "extract_flat": True}
    urls: list[str] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(playlist_url, download=False)
        if result and "entries" in result:
            for entry in result["entries"]:
                if entry and entry.get("url"):
                    urls.append(entry["url"])

    tracks: list[TrackInfo] = []
    for url in urls:
        info = _yt_dlp_track_info(url)
        if info:
            tracks.append(info)
    return tracks


def _yt_dlp_track_info(track_url: str) -> TrackInfo | None:
    ydl_opts = _yt_dmp_opts()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(track_url, download=False)
        except (yt_dlp.utils.ExtractorError, yt_dlp.utils.DownloadError):
            logger.warning("yt-dlp could not extract: %s", track_url)
            return None
    if not info:
        return None
    duration_s = info.get("duration")
    return TrackInfo(
        track_id=str(info.get("id", "")),
        title=info.get("track") or info.get("title", ""),
        artist=info.get("uploader", ""),
        url=track_url,
        duration_s=duration_s,
        genre=info.get("genre") or None,
    )


def refresh_playlist_tracks(playlist: PlaylistInfo) -> PlaylistInfo:
    tracks = fetch_playlist_tracks(playlist.url)
    playlist.tracks = tracks
    return playlist
