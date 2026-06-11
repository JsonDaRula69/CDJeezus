"""StreamFLACr main daemon.

Monitors ALL SoundCloud playlists for the authenticated user, searches
Soulseek for FLAC versions of new tracks (falling back to 320kbps MP3),
downloads them, tags metadata, and creates matching Serato smart crates.

When Serato DJ is running, downloaded files are held in staging until
Serato exits — Serato only scans Auto Import on startup, so importing
while Serato is active would be invisible until restart.

Supports graceful shutdown via `streamflacr stop` (SIGUSR1 + flag file).
"""

import asyncio
import signal
import logging
from pathlib import Path

from .config import (
    DOWNLOAD_DIR,
    STAGING_DIR,
    SEARCH_TIMEOUT,
    SOUNDCLOUD_POLL_INTERVAL,
    SERATO_CHECK_INTERVAL,
)
from .daemon import write_pid, remove_pid, should_stop, clear_stop_flag
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

# Module-level event for graceful shutdown signaling
_stop_event: asyncio.Event | None = None


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

    downloaded: list[Path] = []
    downloaded_versions: set[frozenset[str]] = set()
    max_downloads = 5

    for candidate in candidates:
        if len(downloaded) >= max_downloads:
            break

        versions = extract_versions(candidate["filename"])
        version_key = versions if versions else frozenset({"_no_version"})

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
                logger.info(
                    "Serato is running; holding %s in staging until Serato exits",
                    local_path.name,
                )
                final_path = local_path  # stays in staging
            else:
                dest = DOWNLOAD_DIR / local_path.name
                try:
                    local_path.replace(dest)
                    final_path = dest
                    logger.info("Moved to Auto Import: %s", dest.name)
                except OSError as e:
                    logger.error("Failed to move %s to Auto Import: %s", local_path.name, e)
                    final_path = local_path

            downloaded.append(final_path)
            downloaded_versions.add(version_key)
            state.mark_downloaded(playlist_url, track.track_id, display_artist, track.title, str(final_path))

        # Check for graceful stop between downloads
        if should_stop():
            logger.info("Stop requested; skipping remaining candidates")
            break

    return downloaded


async def sync_playlist(
    playlist: PlaylistInfo,
    slsk: SoulseekDownloader,
    state: StateManager,
    serato_active: bool,
) -> None:
    """Download all new tracks from a playlist that we haven't seen before."""
    tracks = await asyncio.to_thread(fetch_playlist_tracks, playlist.url)

    if not tracks:
        logger.warning("No tracks found for playlist '%s'", playlist.title)
        return

    seen_ids = state.get_seen_ids(playlist.url)
    new_tracks = [t for t in tracks if t.track_id not in seen_ids]

    logger.info("Found %d new track(s) in '%s'", len(new_tracks), playlist.title)

    for track in new_tracks:
        # Check for graceful stop between tracks
        if should_stop():
            logger.info("Stop requested; pausing playlist sync")
            break

        await process_new_track(track, playlist.title, slsk, state, playlist.url, serato_active)

    # Mark all tracks as seen regardless of download success
    state.mark_seen(playlist.url, [t.track_id for t in tracks])
    state.save()


async def _flush_staging_loop(slsk: SoulseekDownloader) -> None:
    """Background task: watch for Serato exit and flush staging files."""
    serato_was_running = is_serato_running()
    notified_pending = False

    while True:
        await asyncio.sleep(SERATO_CHECK_INTERVAL)

        if should_stop():
            break

        serato_active = is_serato_running()

        if serato_active and not serato_was_running:
            serato_was_running = True
            notified_pending = False

        elif not serato_active and serato_was_running:
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

        if serato_active and not notified_pending:
            pending = list(STAGING_DIR.glob("*.flac")) + list(STAGING_DIR.glob("*.mp3"))
            if pending:
                logger.info("%d file(s) waiting in staging for Serato to close", len(pending))
                send_notification(
                    "StreamFLACr",
                    f"{len(pending)} track(s) ready — close Serato DJ to import them",
                )
                notified_pending = True


async def poll_loop(slsk: SoulseekDownloader, state: StateManager) -> None:
    """Main daemon loop: poll playlists, download new tracks, manage staging."""
    # Initial sync
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

    # Flush any files left in staging from a previous run
    if not is_serato_running():
        moved = flush_staging_to_import(STAGING_DIR, DOWNLOAD_DIR)
        if moved:
            logger.info("Flushed %d staged file(s) to Auto Import on startup", len(moved))

    known_playlist_urls = {p.url for p in existing_playlists}

    # Start background Serato watcher
    flush_task = asyncio.create_task(_flush_staging_loop(slsk))

    try:
        while not should_stop():
            # Use event.wait with timeout so SIGUSR1 wakes us immediately
            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=SOUNDCLOUD_POLL_INTERVAL)
                # If we get here, the event was set (stop requested)
                break
            except asyncio.TimeoutError:
                pass  # Normal poll interval elapsed

            if should_stop():
                break

            try:
                current_playlists = await asyncio.to_thread(discover_user_playlists)
            except Exception as e:
                logger.error("Error discovering playlists: %s", e)
                continue

            if should_stop():
                break

            for playlist in current_playlists:
                if should_stop():
                    break
                if playlist.url not in known_playlist_urls:
                    logger.info("New playlist detected: '%s'", playlist.title)
                    state.set_playlist_name(playlist.url, playlist.title)
                    ensure_smart_crate(playlist.title)
                    known_playlist_urls.add(playlist.url)

            serato_active = is_serato_running()
            for playlist in current_playlists:
                if should_stop():
                    break
                try:
                    await sync_playlist(playlist, slsk, state, serato_active)
                except Exception as e:
                    logger.error("Error syncing playlist '%s': %s", playlist.title, e)
    finally:
        flush_task.cancel()


async def run_once(slsk: SoulseekDownloader, state: StateManager) -> None:
    """One-shot sync: process all playlists once and exit."""
    playlists = await asyncio.to_thread(discover_user_playlists)

    serato_active = is_serato_running()
    for playlist in playlists:
        if should_stop():
            logger.info("Stop requested; ending one-shot sync early")
            break
        try:
            await sync_playlist(playlist, slsk, state, serato_active)
        except Exception as e:
            logger.error("Error syncing playlist '%s': %s", playlist.title, e)

    # Flush staging if Serato is not running
    if not is_serato_running():
        moved = flush_staging_to_import(STAGING_DIR, DOWNLOAD_DIR)
        if moved:
            logger.info("Flushed %d staged file(s) to Auto Import", len(moved))


async def graceful_shutdown(slsk: SoulseekDownloader, state: StateManager) -> None:
    """Complete in-progress work and shut down gracefully.

    Called when `streamflacr stop` signals the daemon to stop.
    - Ensures metadata is applied to any untagged files in staging
    - Flushes staging to Auto Import if Serato is not running
    - Notes pending transfers in state if Serato IS running
    - Unloads the LaunchAgent to prevent auto-restart
    """
    logger.info("Graceful shutdown initiated — completing in-progress work")

    # Check for any untagged files in staging and tag them
    untagged = list(STAGING_DIR.glob("*.flac")) + list(STAGING_DIR.glob("*.mp3"))
    if untagged:
        logger.info("Checking %d staged file(s) for missing metadata", len(untagged))
        from .metadata import verify_metadata
        for f in untagged:
            meta = verify_metadata(f)
            if not meta.get("description") and not meta.get("comment"):
                logger.warning("File %s missing playlist comment metadata", f.name)

    # Flush staging to Auto Import if Serato is not running
    if not is_serato_running():
        moved = flush_staging_to_import(STAGING_DIR, DOWNLOAD_DIR)
        if moved:
            logger.info("Flushed %d staged file(s) to Auto Import on shutdown", len(moved))
    else:
        pending = list(STAGING_DIR.glob("*.flac")) + list(STAGING_DIR.glob("*.mp3"))
        if pending:
            logger.info(
                "Serato is running; %d file(s) will remain in staging for next launch",
                len(pending),
            )
            send_notification(
                "StreamFLACr",
                f"{len(pending)} track(s) will import when Serato is restarted",
            )

    # Save state
    state.save()

    # Disconnect from Soulseek
    try:
        await slsk.disconnect()
    except Exception:
        pass


async def amain(daemon: bool = False) -> None:
    """Main entry point: connect to Soulseek and start sync."""
    global _stop_event

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    state = StateManager()
    slsk = SoulseekDownloader(staging_dir=STAGING_DIR)

    # Set up the stop event for SIGUSR1 signaling
    loop = asyncio.get_running_loop()
    _stop_event = asyncio.Event()

    def _sigusr1_handler():
        """Handle SIGUSR1: wake the daemon and set stop flag."""
        logger.info("Received SIGUSR1 — initiating graceful shutdown")
        _stop_event.set()

    # Register SIGUSR1 for graceful stop
    try:
        loop.add_signal_handler(signal.SIGUSR1, _sigusr1_handler)
    except (ValueError, OSError):
        # Signal handling may not work on all platforms
        pass

    # Write PID file
    write_pid()

    try:
        await slsk.connect()

        if daemon:
            await poll_loop(slsk, state)
        else:
            await run_once(slsk, state)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Clean shutdown
        try:
            await graceful_shutdown(slsk, state)
        except (KeyboardInterrupt, asyncio.CancelledError, Exception) as e:
            logger.debug("Error during graceful shutdown: %s", e)
            try:
                await slsk.disconnect()
            except Exception:
                pass
        finally:
            remove_pid()
            clear_stop_flag()

    print("\n  Stopped.")


if __name__ == "__main__":
    from .cli import main
    main()
