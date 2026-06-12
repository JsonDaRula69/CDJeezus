"""CDJeez main daemon.

Monitors ALL SoundCloud playlists for the authenticated user, searches
Soulseek for lossless versions of new tracks (AIFF > WAV > FLAC > MP3 320kbps),
downloads them, tags metadata, and creates matching Serato smart crates.

After downloading, each file is verified via audio fingerprinting
(chromaprint/AcoustID) and embedded metadata comparison. Low-confidence
matches are flagged and the user is notified.

When Serato DJ is running, downloaded files are held in staging until
Serato exits — Serato only scans Auto Import on startup, so importing
while Serato is active would be invisible until restart.

Supports graceful shutdown via `cdjeez stop` (SIGUSR1 + flag file).
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
    FINGERPRINT_VERIFY,
    BACKUP_ENABLED,
    BACKUP_SERATO,
    BACKUP_REKORDBOX,
    PLAYLIST_MODE,
    MONITORED_PLAYLISTS,
    AUTO_UPDATE_INTERVAL,
)
from .daemon import write_pid, remove_pid, should_stop, clear_stop_flag
from .backup import run_backups
from .fingerprint import check_fpcalc, verify_download
from .match import filter_and_rank_candidates, extract_versions
from .metadata import tag_file, enrich_metadata
from .converter import convert_to_aiff, needs_conversion, check_ffmpeg
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
from .style import dim, console

logger = logging.getLogger("cdjeez")

# Module-level event for graceful shutdown signaling
_stop_event: asyncio.Event | None = None

# Log fpcalc availability once at startup
_FPCALC_AVAILABLE: bool | None = None


def _check_fpcalc_available() -> bool:
    """Check fpcalc availability once and cache the result."""
    global _FPCALC_AVAILABLE
    if _FPCALC_AVAILABLE is None:
        _FPCALC_AVAILABLE = check_fpcalc()
        if _FPCALC_AVAILABLE:
            logger.info("fpcalc (chromaprint) available — audio fingerprinting enabled")
        else:
            logger.info("fpcalc not found — install chromaprint for fingerprint verification")
    return _FPCALC_AVAILABLE


def _check_ffmpeg_available() -> bool:
    """Check ffmpeg availability once and log."""
    if check_ffmpeg():
        logger.info("ffmpeg available — FLAC/WAV will be converted to AIFF")
        return True
    logger.warning("ffmpeg not found — FLAC/WAV files won't be converted to AIFF")
    logger.warning("Install: brew install ffmpeg")
    return False


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
    """Search, match, download, verify, tag, and integrate a track.

    After downloading, each file is verified against the SoundCloud track
    using audio fingerprinting (if fpcalc is available) and metadata
    comparison. If verification fails, the next candidate is tried.

    If Serato is running, files stay in staging and are moved to
    Auto Import only after Serato exits.

    Returns list of successfully downloaded paths (in staging or final).
    """
    search_artist = track.canonical_artist or track.artist
    display_artist = track.canonical_artist or track.artist

    logger.info("Processing: %s - %s", display_artist, track.title)

    raw_candidates = await slsk.search_track(search_artist, track.title, timeout=SEARCH_TIMEOUT)

    if not raw_candidates:
        msg = f"No lossless or 320kbps MP3 found: {display_artist} - {track.title}"
        logger.warning(msg)
        send_notification("CDJeez: Not Found", msg)
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
        send_notification("CDJeez: No Match", msg)
        return []

    # Whether to use fingerprint verification
    use_fingerprint = FINGERPRINT_VERIFY and _check_fpcalc_available()

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
            # Verify the download against SoundCloud metadata
            if use_fingerprint or FINGERPRINT_VERIFY:
                result = await asyncio.to_thread(
                    verify_download,
                    local_path,
                    search_artist,
                    track.title,
                    track.duration_s,
                    track.isrc,
                )
                if result.verified:
                    logger.info(
                        "Verified %s (%s, confidence %.2f): %s",
                        local_path.name, result.method, result.confidence,
                        result.notes or "match confirmed",
                    )
                elif result.confidence >= 0.5:
                    logger.warning(
                        "Uncertain match for %s (%s, confidence %.2f): %s",
                        local_path.name, result.method, result.confidence,
                        result.notes or "low confidence",
                    )
                else:
                    logger.warning(
                        "Verification failed for %s (%s, confidence %.2f): %s",
                        local_path.name, result.method, result.confidence,
                        result.notes or "possible wrong version",
                    )
                    # If this is a poor match and we have more candidates,
                    # try the next one instead of keeping a wrong file
                    remaining = [
                        c for c in candidates
                        if (extract_versions(c["filename"]) or frozenset({"_no_version"})) not in downloaded_versions
                        and c != candidate
                    ]
                    if remaining and len(downloaded) == 0:
                        logger.info(
                            "Skipping poor match, trying next candidate for %s - %s",
                            display_artist, track.title,
                        )
                        # Delete the bad download from staging
                        try:
                            local_path.unlink()
                            logger.debug("Removed unverified file: %s", local_path.name)
                        except OSError:
                            pass
                        continue

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

    # If no downloads succeeded but we had candidates, notify the user
    if not downloaded and candidates:
        best = candidates[0]
        msg = f"Could not verify any match for {display_artist} - {track.title}"
        logger.warning(msg)
        send_notification("CDJeez: Uncertain Match", msg)

    return downloaded


async def sync_playlist(
    playlist: PlaylistInfo,
    slsk: SoulseekDownloader,
    state: StateManager,
    serato_active: bool,
) -> None:
    """Download all new tracks from a playlist that we haven't seen before."""
    # Skip playlists not in the monitored list (when using custom mode)
    if PLAYLIST_MODE == "custom" and playlist.url not in MONITORED_PLAYLISTS:
        logger.debug("Skipping unmonitored playlist: '%s'", playlist.title)
        return

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
    """Periodically check if Serato has exited and flush staged files."""
    while not should_stop():
        await asyncio.sleep(SERATO_CHECK_INTERVAL)
        if should_stop():
            break
        if is_serato_running():
            continue
        moved = flush_staging_to_import(STAGING_DIR, DOWNLOAD_DIR)
        if moved:
            logger.info("Flushed %d staged file(s) to Auto Import (Serato exited)", len(moved))
        # Only flush once after Serato exits, then check again next cycle
        if moved:
            # Give Serato time to import before checking again
            await asyncio.sleep(30)


async def poll_loop(slsk: SoulseekDownloader, state: StateManager) -> None:
    """Main daemon loop: poll for new playlists and process them."""
    known_playlist_urls: set[str] = set()

    # Start Serato-aware staging flush in background
    flush_task = asyncio.create_task(_flush_staging_loop(slsk))

    try:
        while not should_stop():
            playlists = await asyncio.to_thread(discover_user_playlists)

            if not playlists:
                logger.info("No playlists found; will retry on next poll")
            else:
                # Discover new playlists and create smart crates
                for playlist in playlists:
                    if playlist.url not in known_playlist_urls:
                        logger.info("New playlist detected: '%s'", playlist.title)
                        state.set_playlist_name(playlist.url, playlist.title)
                        ensure_smart_crate(playlist.title)
                        known_playlist_urls.add(playlist.url)

            serato_active = is_serato_running()
            for playlist in playlists:
                if should_stop():
                    break
                try:
                    await sync_playlist(playlist, slsk, state, serato_active)
                except Exception as e:
                    logger.error("Error syncing playlist '%s': %s", playlist.title, e)

            # Wait for next poll cycle, but wake early on SIGUSR1
            if _stop_event is not None:
                try:
                    await asyncio.wait_for(_stop_event.wait(), timeout=SOUNDCLOUD_POLL_INTERVAL)
                    # If we get here, stop was requested
                    break
                except asyncio.TimeoutError:
                    # Normal poll cycle — check for auto-update
                    from .updater import auto_update_if_available
                    try:
                        if auto_update_if_available():
                            logger.info("Auto-update scheduled, shutting down for upgrade")
                            from .notify import send_notification
                            send_notification(
                                "CDJeez",
                                "Restarting to apply update...",
                            )
                            # Trigger graceful shutdown
                            if _stop_event is not None:
                                _stop_event.set()
                            break
                    except Exception as e:
                        logger.debug("Auto-update check failed: %s", e)
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

    Called when `cdjeez stop` signals the daemon to stop.
    - Ensures metadata is applied to any untagged files in staging
    - Flushes staging to Auto Import if Serato is not running
    - Notes pending transfers in state if Serato IS running
    - Unloads the LaunchAgent to prevent auto-restart
    """
    logger.info("Graceful shutdown initiated — completing in-progress work")

    # Check for any untagged files in staging and tag them
    untagged = (list(STAGING_DIR.glob("*.flac")) + list(STAGING_DIR.glob("*.mp3"))
                + list(STAGING_DIR.glob("*.aiff")) + list(STAGING_DIR.glob("*.aif"))
                + list(STAGING_DIR.glob("*.wav")))
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
        pending = (list(STAGING_DIR.glob("*.flac")) + list(STAGING_DIR.glob("*.mp3"))
                   + list(STAGING_DIR.glob("*.aiff")) + list(STAGING_DIR.glob("*.aif"))
                   + list(STAGING_DIR.glob("*.wav")))
        if pending:
            logger.info(
                "Serato is running; %d file(s) will remain in staging for next launch",
                len(pending),
            )
            send_notification(
                "CDJeez",
                f"{len(pending)} track(s) will import when Serato is restarted",
            )

    # Save state
    state.save()

    # Run post-session backup if enabled
    if BACKUP_ENABLED:
        logger.info("Running post-session backup...")
        await asyncio.to_thread(
            run_backups,
            do_serato=BACKUP_SERATO,
            do_rekordbox=BACKUP_REKORDBOX,
        )

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

    # Check for and perform any pending auto-update before starting
    from .updater import perform_pending_update
    if perform_pending_update():
        logger.info("Auto-update performed, restarting with new version")
        # The new version will be picked up on next launch
        remove_pid()
        clear_stop_flag()
        return

    # Check conversion tools
    _check_ffmpeg_available()

    # Run pre-session backup if enabled
    if BACKUP_ENABLED:
        logger.info("Running pre-session backup...")
        await asyncio.to_thread(
            run_backups,
            do_serato=BACKUP_SERATO,
            do_rekordbox=BACKUP_REKORDBOX,
        )

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

    console.print()
    dim("Stopped.")


if __name__ == "__main__":
    from .cli import main
    main()
