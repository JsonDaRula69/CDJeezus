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

import time

import requests

logger = logging.getLogger(__name__)

# Rate limit: max 600 requests per 10 minutes = 1 request per second average
# We use a simple approach: track last request time and sleep if needed
_last_request_time: float = 0.0
_min_request_interval: float = 1.0  # seconds between API calls

def _rate_limit() -> None:
    """Ensure we don't exceed SoundCloud API rate limits."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _min_request_interval:
        time.sleep(_min_request_interval - elapsed)
    _last_request_time = time.monotonic()

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
    label_name: str | None = None  # record label from SoundCloud


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
    Rate limited to ~1 req/sec to stay within API limits.
    """
    _rate_limit()
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

def _track_from_api(track_data: dict) -> TrackInfo | None:
    """Build a TrackInfo from a SoundCloud API track object.

    Returns None if the track has no title or artist (incomplete API data).
    """
    title = track_data.get("title", "")
    artist = track_data.get("user", {}).get("username", "")
    if not title or not artist:
        return None
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
    label_name = track_data.get("label_name") or None

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
        label_name=label_name,
    )


# ── SoundCloud session refresh ───────────────────────────────────────────

def _refresh_soundcloud_session() -> bool:
    """Refresh the SoundCloud session by reloading an existing tab or opening the app.

    Returns True if a session was found and reloaded/opened.
    Strategy:
    1. If the SoundCloud PWA app is running, reload it via AppleScript
    2. If Chrome has any SoundCloud tabs, reload one
    3. If neither, open the SoundCloud PWA app or Chrome
    """
    import subprocess

    # Try reloading an existing SoundCloud tab via AppleScript
    # This works for both PWA app windows and regular Chrome tabs
    reload_script = '''
    tell application "Google Chrome"
        set reloaded to false
        repeat with w in windows
            repeat with t in tabs of w
                if URL of t contains "soundcloud.com" then
                    tell t to reload
                    set reloaded to true
                    exit repeat
                end if
            end repeat
            if reloaded then exit repeat
        end repeat
        return reloaded
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", reload_script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip().lower() == "true":
            logger.info("Reloaded existing SoundCloud tab to refresh OAuth token")
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug("AppleScript reload attempt failed: %s", e)

    # No existing SoundCloud tab found — open the PWA app or Chrome
    sc_app = Path.home() / "Applications" / "Chrome Apps.localized" / "SoundCloud.app"
    if sc_app.exists():
        logger.info("No SoundCloud tab found; launching SoundCloud app")
        subprocess.run(["open", "-gja", str(sc_app)], capture_output=True, check=False)
    else:
        logger.info("No SoundCloud tab found; launching Chrome to SoundCloud")
        subprocess.run(["open", "https://soundcloud.com"], capture_output=True, check=False)
    return True


# ── Playlist discovery ──────────────────────────────────────────────────

def _get_user_id() -> int | None:
    """Get the authenticated user's SoundCloud ID via /me.

    If the initial attempt fails (OAuth token may be stale or Chrome not running),
    refreshes the SoundCloud session and retries up to 3 times.
    First retry waits 10s (quick refresh), subsequent retries wait 30s.
    After all retries fail, sends a macOS notification.
    """
    data = _api_get("me")
    if data:
        return data.get("id")

    # OAuth failed — refresh the SoundCloud session to get a fresh token
    _refresh_soundcloud_session()

    for attempt in range(1, 4):
        # Shorter wait on first retry (tab was just reloaded, token refreshes quickly)
        # Longer waits for subsequent retries (Chrome may have been freshly opened)
        wait = 10 if attempt == 1 else 30
        logger.info("OAuth retry %d/3 in %ds (waiting for session refresh)...", attempt, wait)
        time.sleep(wait)

        # Clear cached OAuth token so we re-read cookies
        global _cached_oauth_token, _cached_cookies
        _cached_oauth_token = None
        _cached_cookies = None

        data = _api_get("me")
        if data:
            logger.info("OAuth succeeded on retry %d", attempt)
            return data.get("id")

    # All retries exhausted — notify the user
    from .notify import send_notification
    send_notification(
        "StreamFLACr: SoundCloud Auth Failed",
        "Could not connect to SoundCloud. Please make sure you're signed into SoundCloud in Chrome.",
    )
    logger.error("Could not identify SoundCloud user after 3 retries with session refresh")
    return None


def discover_user_playlists(user_sets_url: str | None = None) -> list[PlaylistInfo]:
    """Discover all playlists for the authenticated user via API v2.

    Paginates through all playlists using linked_partitioning.
    """
    user_id = _get_user_id()
    if not user_id:
        return []

    all_playlists: list[PlaylistInfo] = []
    url: str | None = f"https://api-v2.soundcloud.com/users/{user_id}/playlists?limit=50&representation=full"

    while url:
        data = _api_get_raw_url(url)
        if not data:
            break

        playlists_raw: list[dict]
        if isinstance(data, list):
            playlists_raw = data
        elif isinstance(data, dict):
            playlists_raw = data.get("collection", data.get("playlists", []))
            # Handle linked_partitioning (next_href)
            url = data.get("next_href")
        else:
            break

        for p in playlists_raw:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id", ""))
            title = p.get("title", "?")
            permalink = p.get("permalink_url", "")
            if not (pid and permalink):
                continue
            tracks: list[TrackInfo] = []
            raw_tracks = p.get("tracks", [])
            if isinstance(raw_tracks, list):
                tracks = [t for t in (_track_from_api(x) for x in raw_tracks if isinstance(x, dict)) if t is not None]
            all_playlists.append(PlaylistInfo(playlist_id=pid, title=title, url=permalink, tracks=tracks))

    logger.info("Discovered %d playlists for user %d", len(all_playlists), user_id)
    return all_playlists


def _api_get_raw_url(url: str) -> dict | None:
    """Make a rate-limited GET request to a full SoundCloud API URL.

    Handles OAuth/client_id auth like _api_get, but takes a full URL
    instead of an endpoint path. Used for linked_partitioning pagination.
    """
    _rate_limit()
    token = _get_oauth_token()

    # Attempt 1: OAuth header
    if token:
        headers = {"Authorization": f"OAuth {token}"}
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("OAuth attempt on %s returned %d", url, resp.status_code)
        except requests.RequestException as e:
            logger.debug("OAuth request failed on %s: %s", url, e)

    # Attempt 2: client_id appended as query param
    client_id = _get_client_id()
    sep = "&" if "?" in url else "?"
    full_url = f"{url}{sep}client_id={client_id}"
    try:
        resp = requests.get(full_url, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        logger.debug("client_id attempt on %s returned %d", url, resp.status_code)
    except requests.RequestException as e:
        logger.debug("client_id request failed on %s: %s", url, e)

    return None


# ── Playlist track fetching ──────────────────────────────────────────────

def fetch_playlist_tracks(playlist_url: str) -> list[TrackInfo]:
    """Fetch all tracks from a SoundCloud playlist via API v2.

    SoundCloud caps the number of tracks returned inline in playlist
    objects (~5-10). Strategy:
    1. Resolve the playlist URL to get its ID
    2. Fetch full playlist data with representation=full
    3. If we still have fewer tracks than track_count, extract all
       track IDs from the playlist data and batch-fetch missing ones
    """
    # Resolve the playlist URL to get its ID
    data = _api_get("resolve", {"url": playlist_url})
    if not data or not data.get("id"):
        logger.warning("Could not resolve playlist: %s", playlist_url)
        return []

    pid = data["id"]
    title = data.get("title", "")
    track_count = data.get("track_count", 0)

    # Fetch the full playlist with representation=full to get all track data
    full_data = _api_get(f"playlists/{pid}", {"representation": "full"})
    if full_data and full_data.get("id"):
        data = full_data

    # Collect track objects and track IDs from all available sources
    all_tracks: list[TrackInfo] = []
    seen_ids: set[str] = set()

    # Extract tracks from the full playlist data
    raw_tracks = data.get("tracks", [])
    if isinstance(raw_tracks, list):
        for t in _extract_tracks_with_ids(raw_tracks, seen_ids):
            all_tracks.append(t)

    if len(all_tracks) >= track_count and track_count > 0:
        logger.info("API returned %d tracks for playlist '%s' (id=%s)", len(all_tracks), title, pid)
        return all_tracks

    # Still missing tracks — extract ALL track IDs and batch-fetch
    all_ids = _collect_track_ids(data)
    missing_ids = [tid for tid in all_ids if tid not in seen_ids]

    if missing_ids:
        logger.info(
            "Playlist '%s' has %d tracks but only %d inline; fetching %d by ID",
            title, track_count, len(all_tracks), len(missing_ids),
        )
        for i in range(0, len(missing_ids), 50):
            batch = missing_ids[i:i + 50]
            ids_param = ",".join(batch)
            batch_result = _api_get("tracks", {"ids": ids_param})
            if batch_result and isinstance(batch_result, list):
                for t in _extract_tracks_with_ids(batch_result, seen_ids):
                    all_tracks.append(t)

    # Last resort: if we still don't have enough and track_count > 0,
    # try the dedicated tracks endpoint with pagination
    if track_count > 0 and len(all_tracks) < track_count:
        logger.info(
            "Still missing tracks for '%s' (%d/%d); trying /playlists/{id}/tracks",
            title, len(all_tracks), track_count,
        )
        offset = 0
        while len(all_tracks) < track_count:
            result = _api_get(f"playlists/{pid}/tracks", {"limit": 50, "offset": offset})
            if result is None:
                break
            page = result if isinstance(result, list) else result.get("collection", []) if isinstance(result, dict) else []
            page_tracks = [t for t in (_track_from_api(x) for x in page if isinstance(x, dict)) if t is not None]
            for t in page_tracks:
                if t.track_id not in seen_ids:
                    seen_ids.add(t.track_id)
                    all_tracks.append(t)
            if len(page_tracks) < 50:
                break
            offset += 50

    logger.info("Fetched %d tracks for playlist '%s' (id=%s, expected=%d)", len(all_tracks), title, pid, track_count)
    return all_tracks


def _extract_tracks_with_ids(raw_tracks: list, seen_ids: set[str]) -> list[TrackInfo]:
    """Parse track objects from API data, skipping duplicates by track ID."""
    result: list[TrackInfo] = []
    for item in raw_tracks:
        if not isinstance(item, dict):
            continue
        track = _track_from_api(item)
        if track and track.track_id not in seen_ids:
            seen_ids.add(track.track_id)
            result.append(track)
    return result


def _collect_track_ids(data: dict) -> list[str]:
    """Extract all track IDs from a playlist response, from any field that may contain them."""
    ids: list[str] = []

    # Primary: tracks array (may have objects with just id)
    for t in data.get("tracks", []):
        if isinstance(t, dict):
            tid = str(t.get("id", ""))
            if tid:
                ids.append(tid)

    # Some responses include tracks_data with minimal info
    for t in data.get("tracks_data", []):
        if isinstance(t, dict):
            tid = str(t.get("id", ""))
            if tid:
                ids.append(tid)

    # Some responses include a track_ids list of plain integers
    for tid in data.get("track_ids", []):
        ids.append(str(tid))

    return ids


def refresh_playlist_tracks(playlist: PlaylistInfo) -> PlaylistInfo:
    """Re-fetch tracks for a playlist."""
    tracks = fetch_playlist_tracks(playlist.url)
    playlist.tracks = tracks
    return playlist
