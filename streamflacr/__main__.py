"""StreamFLACr main daemon.

Monitors ALL SoundCloud playlists for the authenticated user, searches
Soulseek for FLAC versions of new tracks (falling back to 320kbps MP3),
downloads them, tags metadata, and creates matching Serato smart crates.

When Serato DJ is running, downloaded files are held in staging until
Serato exits — Serato only scans Auto Import on startup, so importing
while Serato is active would be invisible until restart.
"""

import asyncio
import logging
from pathlib import Path

from .config import (
    DOWNLOAD_DIR,
    STAGING_DIR,
    SEARCH_TIMEOUT,
    SOUNDCLOUD_POLL_INTERVAL,
    SERATO_CHECK_INTERVAL,
)
from .match import filter_and_rank_candidates, extract_versions
from .metadata import tag_file, enrich_metadata
from .notify import send_notification
from .serato_crate import ensure_smart_crate
from .serato_watch import is_serato_running, flush_staging_to_import
from .soundcloud import (
    PlaylistInfo,
    TrackInfo,
    discover_user_playlists,
    fetch_playlist_tracks,
)
from .soulseek import SoulseekDownloader
from .state import StateManager

logger = logging.getLogger("streamflacr")


def _version_label(filename: str) -> str:
    """Build a short version label from the filename for the notification."""
    versions = extract_versions(filename)
    if not versions:
        return ""
    tags = sorted(v.title() for v in versions)
    return " (" + ", ".join(tags) + ")"


async def process_new_track(
    track: TrackInfo,
    playlist_name: str,
    slsk: SoulseekDownloader,
    state: StateManager,
    playlist_url: str,
    serato_active: bool,
) -> list[Path]:
    """Search, match, download, tag, and integrate a track.

    If Serato is running, files stay in staging and are moved to
    Auto Import only after Serato exits.

    Returns list of successfully downloaded paths (in staging or final).
    """
    # Use canonical_artist (publisher_metadata.artist) for search and display
    # since track.artist is the SoundCloud profile handle (e.g. "heisrema")
    # while canonical_artist is the real name (e.g. "Rema")
    search_artist = track.canonical_artist or track.artist
    display_artist = track.canonical_artist or track.artist

    logger.info("Processing: %s - %s", display_artist, track.title)

    raw_candidates = await slsk.search_track(search_artist, track.title, timeout=SEARCH_TIMEOUT)

    if not raw_candidates:
        msg = f"No FLAC or 320kbps MP3 found: {display_artist} - {track.title}"
        logger.warning(msg)
        send_notification("StreamFLACr: Not Found", msg)
        return []

    candidates = filter_and_rank_candidates(
        sc_artist=search_artist,
        sc_title=track.title,
        sc_duration_s=track.duration_s,
        candidates=raw_candidates,
    )

    if not candidates:
        msg = f"No matching result on Soulseek: {display_artist} - {track.title}"
        logger.warning(msg)
        send_notification("StreamFLACr: No Match", msg)
        return []

    # Download candidates, stopping once we have one per version group
    downloaded: list[Path] = []
    downloaded_versions: set[frozenset[str]] = set()
    max_downloads = 5  # safety cap per track

    for candidate in candidates:
        if len(downloaded) >= max_downloads:
            break

        versions = extract_versions(candidate["filename"])
        version_key = versions if versions else frozenset({"_no_version"})

        # Skip if we already have this version
        if version_key in downloaded_versions:
            continue

        fmt = "FLAC" if candidate["tier"] == 0 else f"{candidate['bitrate']}kbps MP3"
        ver_label = _version_label(candidate["filename"])
        logger.info(
            "Trying (score %.2f, %s%s): %s from %s",
            candidate["match_score"], fmt, ver_label,
            candidate["filename"], candidate["username"],
        )

        local_path = await slsk.download(candidate["username"], candidate["remote_path"])
        if local_path and local_path.exists():
            # Tag metadata while file is still in staging dir
            tag_file(
                filepath=local_path,
                artist=display_artist,
                title=track.title,
                playlist_name=playlist_name,
                album=track.album,
                genre=track.genre,
                label_name=track.label_name,
            )
            enrich_metadata(
                filepath=local_path,
                sc_track=track,
                playlist_name=playlist_name,
            )

            if serato_active:
                # Serato is running — keep in staging, don't move to Auto Import yet.
                # The daemon will flush staging after Serato exits.
                logger.info(
                    "Serato is running; holding %s in staging until Serato exits",
                    local_path.name,
                )
                final_path = local_path  # stays in staging
            else:
                # Serato not running — move to Auto Import now (atomic replace)
                final_path = DOWNLOAD_DIR / local_path.name
                local_path.replace(final_path)

            state.mark_downloaded(
                playlist_url=playlist_url,
                track_id=track.track_id,
                artist=display_artist,
                title=track.title,
                local_path=str(final_path),
            )

            downloaded_versions.add(version_key)
            downloaded.append(final_path)

            quality = "FLAC" if final_path.suffix.lower() == ".flac" else "320kbps MP3"
            if serato_active:
                send_notification(
                    "StreamFLACr",
                    f"Downloaded ({quality}{ver_label}): {display_artist} - {track.title}"
                    f" (will import when Serato closes)",
                )
            else:
                send_notification(
                    "StreamFLACr",
                    f"Downloaded ({quality}{ver_label}): {display_artist} - {track.title}",
                )
        # If download failed, continue to next candidate (same or different version)

    if not downloaded:
        msg = f"All download attempts failed: {display_artist} - {track.title}"
        logger.error(msg)
        send_notification("StreamFLACr: Download Failed", msg)
    elif len(downloaded) > 1:
        logger.info(
            "Downloaded %d versions for '%s - %s' (user can delete duplicates)",
            len(downloaded), display_artist, track.title,
        )
        send_notification(
            "StreamFLACr",
            f"{len(downloaded)} versions downloaded: {display_artist} - {track.title}",
        )

    return downloaded


async def sync_playlist(
    playlist: PlaylistInfo,
    slsk: SoulseekDownloader,
    state: StateManager,
    serato_active: bool,
) -> None:
    playlist_url = playlist.url
    playlist_name = playlist.title

    ensure_smart_crate(playlist_name)

    # Run SoundCloud API call in a thread to avoid blocking the event loop
    tracks = await asyncio.to_thread(fetch_playlist_tracks, playlist_url)
    if not tracks:
        logger.debug("No tracks found in playlist: %s", playlist_name)
        return

    current_ids = {t.track_id for t in tracks}
    seen_ids = state.get_seen_ids(playlist_url)
    new_ids = current_ids - seen_ids

    if not new_ids:
        return

    logger.info("Found %d new track(s) in '%s'", len(new_ids), playlist_name)
    new_tracks = [t for t in tracks if t.track_id in new_ids]

    # Process tracks concurrently (up to 3 simultaneous downloads)
    semaphore = asyncio.Semaphore(3)

    async def _process_with_semaphore(t: TrackInfo) -> None:
        async with semaphore:
            try:
                await process_new_track(t, playlist_name, slsk, state, playlist_url, serato_active)
            except Exception as e:
                logger.error("Error processing track %s: %s", t.title, e)

    tasks = [_process_with_semaphore(t) for t in new_tracks]
    await asyncio.gather(*tasks)

    state.mark_seen(playlist_url, list(new_ids))
    state.save()


async def _flush_staging_loop(slsk: SoulseekDownloader) -> None:
    """Background task that checks if Serato has exited and flushes staging files.

    While Serato is running, downloaded files stay in the staging directory.
    Once Serato exits, we move them all to Auto Import so Serato picks them
    up on next launch.
    """
    serato_was_running = False
    notified_pending = False

    while True:
        serato_active = is_serato_running()

        if serato_active and not serato_was_running:
            logger.info("Serato DJ detected as running — files will be held in staging")
            serato_was_running = True
            notified_pending = False

        elif not serato_active and serato_was_running:
            # Serato just exited — flush all staging files to Auto Import
            logger.info("Serato DJ exited — flushing staging files to Auto Import")
            moved = flush_staging_to_import(STAGING_DIR, DOWNLOAD_DIR)
            if moved:
                logger.info("Moved %d file(s) to Auto Import", len(moved))
                send_notification(
                    "StreamFLACr",
                    f"Imported {len(moved)} track(s) to Serato",
                )
            serato_was_running = False
            notified_pending = False

        # Notify once per Serato session if there are pending files
        if serato_active and not notified_pending:
            pending = list(STAGING_DIR.glob("*.flac")) + list(STAGING_DIR.glob("*.mp3"))
            if pending:
                logger.info("%d file(s) waiting in staging for Serato to close", len(pending))
                send_notification(
                    "StreamFLACr",
                    f"{len(pending)} track(s) ready — close Serato DJ to import them",
                )
                notified_pending = True

        await asyncio.sleep(SERATO_CHECK_INTERVAL)


async def poll_loop(slsk: SoulseekDownloader, state: StateManager) -> None:
    # Initial sync: discover all playlists and mark existing tracks as seen
    existing_playlists = await asyncio.to_thread(discover_user_playlists)

    for playlist in existing_playlists:
        tracks = await asyncio.to_thread(fetch_playlist_tracks, playlist.url)
        playlist.tracks = tracks
        state.set_playlist_name(playlist.url, playlist.title)
        state.mark_seen(playlist.url, [t.track_id for t in tracks])
        ensure_smart_crate(playlist.title)
    state.save()

    total_tracks = sum(len(p.tracks) for p in existing_playlists)
    logger.info(
        "Initial sync: %d playlists, %d tracks already known",
        len(existing_playlists),
        total_tracks,
    )
    send_notification("StreamFLACr", f"Watching {len(existing_playlists)} playlists")

    # Flush any files left in staging from a previous run (Serato may not be running now)
    if not is_serato_running():
        moved = flush_staging_to_import(STAGING_DIR, DOWNLOAD_DIR)
        if moved:
            logger.info("Flushed %d staged file(s) to Auto Import on startup", len(moved))

    known_playlist_urls = {p.url for p in existing_playlists}

    # Start background task to watch for Serato exit and flush staging
    flush_task = asyncio.create_task(_flush_staging_loop(slsk))

    try:
        while True:
            await asyncio.sleep(SOUNDCLOUD_POLL_INTERVAL)

            try:
                current_playlists = await asyncio.to_thread(discover_user_playlists)
            except Exception as e:
                logger.error("Error discovering playlists: %s", e)
                continue

            for playlist in current_playlists:
                if playlist.url not in known_playlist_urls:
                    logger.info("New playlist detected: '%s'", playlist.title)
                    state.set_playlist_name(playlist.url, playlist.title)
                    ensure_smart_crate(playlist.title)
                    known_playlist_urls.add(playlist.url)

            serato_active = is_serato_running()
            for playlist in current_playlists:
                try:
                    await sync_playlist(playlist, slsk, state, serato_active)
                except Exception as e:
                    logger.error("Error syncing playlist '%s': %s", playlist.title, e)
    finally:
        flush_task.cancel()


async def run_once(slsk: SoulseekDownloader, state: StateManager) -> None:
    playlists = await asyncio.to_thread(discover_user_playlists)

    serato_active = is_serato_running()
    for playlist in playlists:
        try:
            await sync_playlist(playlist, slsk, state, serato_active)
        except Exception as e:
            logger.error("Error syncing playlist '%s': %s", playlist.title, e)

    # Flush staging if Serato is not running
    if not serato_active:
        moved = flush_staging_to_import(STAGING_DIR, DOWNLOAD_DIR)
        if moved:
            logger.info("Flushed %d staged file(s) to Auto Import", len(moved))


async def amain(daemon: bool = False) -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    state = StateManager()
    slsk = SoulseekDownloader(staging_dir=STAGING_DIR)

    try:
        await slsk.connect()

        if daemon:
            await poll_loop(slsk, state)
        else:
            await run_once(slsk, state)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        try:
            await slsk.disconnect()
        except (KeyboardInterrupt, asyncio.CancelledError, Exception):
            pass


if __name__ == "__main__":
    from .cli import main
    main()
