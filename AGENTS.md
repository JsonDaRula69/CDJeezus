# StreamFLACr — Project Knowledge Base

**Last updated:** v0.27.0
**Stack:** Python 3.11+, macOS, aioslsk, mutagen, serato-tools, pydantic-settings

## Overview

StreamFLACr monitors SoundCloud playlists for new tracks, searches Soulseek for FLAC versions (falling back to 320kbps MP3), downloads them, tags metadata, and creates matching Serato smart crates. macOS-only (uses Chrome cookie decryption, osascript notifications, launchd).

## Structure

```
streamflacr/
├── __init__.py          # Version
├── __main__.py          # Daemon: poll loop, track processing, graceful shutdown, auto-update check
├── cli.py               # Argparse entry point, logging config, instance detection, stop/log commands
├── config.py            # Env-based config via .env in ~/.config/streamflacr/
├── daemon.py            # PID tracking, stop signaling (SIGUSR1 + flag file), single-instance, log tailing
├── fingerprint.py       # Audio fingerprinting via chromaprint/AcoustID for download verification
├── backup.py            # Library backup system (zip Serato/Rekordbox metadata to ~/Music/LibraryBackups)
├── library_scan.py      # Local library scanning and AcoustID fingerprint assignment
├── soundcloud.py        # API v2 with dual-attempt auth (OAuth first, client_id fallback)
├── soulseek.py           # Search/download via aioslsk; graceful port conflict handling
├── match.py              # Fuzzy matching: filename parsing, version descriptors, scoring
├── metadata.py           # FLAC (Vorbis) + MP3 (ID3v2) tagging; verify + enrich from SC data
├── serato_crate.py       # Smart crate: Comment IS <playlist_name> rule
├── serato_watch.py       # Detect Serato running; flush staging → Auto Import on exit
├── notify.py             # macOS notifications via osascript
├── setup.py              # Interactive setup wizard (8 steps), full_uninstall(), LaunchDaemon management
├── state.py              # JSON state file tracking seen tracks, download history, verification status
└── updater.py             # Self-update: check PyPI, auto-update daemon, migrate data, CLI update command
```

## Where to Look

| Task | File | Key function |
|------|------|---------------|
| Add a new SoundCloud API endpoint | `soundcloud.py` | `_api_get()` |
| Change download quality logic | `soulseek.py` | `search_track()` — `MIN_MP3_BITRATE = 320` |
| Change matching algorithm | `match.py` | `filter_and_rank_candidates()` — `HIGH_CONFIDENCE_SCORE = 0.70` |
| Change what metadata gets tagged | `metadata.py` | `tag_file()`, `enrich_metadata()` |
| Change Serato crate behavior | `serato_crate.py` | `ensure_smart_crate()` |
| Change Serato-aware staging | `serato_watch.py` | `flush_staging_to_import()`, `is_serato_running()` |
| Change graceful stop behavior | `daemon.py` | `request_stop()`, `should_stop()`, `is_running()` |
| Change CLI flags | `cli.py` | `main()` — argparse |
| Change daemon poll interval | `config.py` | `SOUNDCLOUD_POLL_INTERVAL` (default 300s) |
| Change backup rotation | `backup.py` | `MAX_BACKUPS = 10` |
| Fix OAuth auth flow | `soundcloud.py` | `_get_user_id()` — Chrome launch + 3 retries (15/20/25s) |
| Fix setup wizard steps | `setup.py` | `run_setup()` — 8 steps |
| Change state schema | `state.py` + `updater.py` | `STATE_VERSION`, `_migrate_state()` |
| Change fingerprint verification | `fingerprint.py` | `verify_download()`, `check_fpcalc()`, `lookup_acoustid()` |
| Change AcoustID config | `config.py` | `ACOUSTID_API_KEY`, `FINGERPRINT_VERIFY` |
| Change library backup | `backup.py` | `run_backups()`, `backup_serato()`, `backup_rekordbox()` |
| Change library scanning | `library_scan.py` | `scan_serato_library()`, `fingerprint_library_tracks()` |
| Change DJ software config | `config.py` | `PRIMARY_DJ`, `TWO_WAY_SYNC`, `REKORDBOX_DIR` |
| Change playlist mode | `config.py` | `PLAYLIST_MODE`, `MONITORED_PLAYLISTS` |
| Change auto-update interval | `config.py` | `AUTO_UPDATE_INTERVAL` (default 14400s = 4 hours) |

## Architecture & Design Decisions

### Auto-Update
- Daemon checks PyPI for new versions on startup and every `AUTO_UPDATE_INTERVAL` seconds (default 4 hours)
- On startup: `perform_pending_update()` checks for an `auto-update-pending` flag file. If found, upgrades the package, migrates data, and reloads the LaunchAgent before starting the main loop
- During poll loop: `auto_update_if_available()` checks PyPI. If a new version is found, writes the `auto-update-pending` flag and triggers a graceful shutdown. The LaunchAgent relaunches the new version which then performs the upgrade
- Manual update: `streamflacr update` stops the daemon, upgrades via uv/pip, migrates data, and restarts
- `.env` migration: `_migrate_env()` adds missing config keys with defaults, preserving existing values

### Graceful Shutdown (`streamflacr stop`)
- `streamflacr stop` writes a `stop-requested` flag file and sends SIGUSR1 to the daemon PID
- The daemon's SIGUSR1 handler sets an asyncio.Event (`_stop_event`) which wakes the poll loop
- The poll loop and download processing check `should_stop()` between operations
- On shutdown: completes in-progress downloads, applies metadata, flushes staging to Auto Import (unless Serato is running), notes pending transfers in state.json, disconnects from Soulseek, unloads LaunchAgent to prevent auto-restart
- PID file at `~/.config/streamflacr/streamflacr.pid`

### Single-Instance Behavior
- When `streamflacr` is run and a daemon is already running, it tails the log file instead of starting a duplicate
- `--force` flag overrides this and starts a new instance anyway
- Log file at `~/.config/streamflacr/streamflacr.log` (rotating, 5MB, 3 backups)

### SoundCloud Auth (dual-attempt)
- `_api_get()` tries OAuth first (from Chrome cookies), then falls back to client_id
- If OAuth fails, launches the SoundCloud PWA app (`~/Applications/Chrome Apps.localized/SoundCloud.app`) to refresh the session
- Retries up to 3 times with 15/20/25 second delays between attempts
- Never sends OAuth + client_id together (causes 403)

### Backup System
- **Library backups** (`backup.py`): Zip Serato/Rekordbox metadata (not audio) to `~/Music/LibraryBackups`. Runs before/after sessions and on DJ software exit. Max 10 backups.
- **Scrate backups**: Removed separate backup system. Scrate files are covered by library backups.
- **Uninstall**: Never removes backup directories. Only removes `~/.config/streamflacr/`, staging, plist, and pid/log files.

### Uninstaller (`streamflacr uninstall`)
- Single prompt: "Keep streaming source migration data? [Y/n]"
- If yes: keeps `state.json` and `.env`, removes everything else in config dir
- If no: removes entire config dir including state.json and .env
- Always removes: LaunchAgent plist, pid file, log file, staging dir
- Never removes: `~/Music/LibraryBackups/`, `~/Music/_Serato_/`, `~/Music/_Serato_/SmartCrates/`
- Never removes: downloaded music files, Rekordbox library data

### Setup Wizard (8 steps)
1. Primary DJ software (auto-detected if only one found)
2. 2-way sync with secondary DJ
3. Soulseek credentials
4. AcoustID API key
5. SoundCloud login
6. Playlist selection (TUI with arrow keys/spacebar/enter)
7. Library backups
8. Config summary with edit loop + disclaimer page

### Installer/Updater/Uninstaller Maintenance
- Whenever code changes modify `state.json` schema: add migration step in `updater.py` `_migrate_state()` and increment `CURRENT_STATE_VERSION`
- Whenever new config keys are added: add defaults to `updater.py` `_migrate_env()` and `setup.py` `write_env_file()`
- Whenever new CLI commands or flags are added: update `cli.py` argparse
- Whenever uninstall behavior changes: update `full_uninstall()` — ensure Serato/Rekordbox data is never touched
- Before every git commit: use `$omo:debugging` and `$omo:remove-ai-slops` tools

## Release Process
1. Bump version in `__init__.py` AND `pyproject.toml`
2. Use `$omo:debugging` and `$omo:remove-ai-slops` skills before committing
3. Commit with `v<version>` message
4. Create feature branch, push, PR via `gh pr create`, merge with `--admin --squash`
5. `gh release create v<version>`
6. GitHub Actions publishes to PyPI via trusted publishing (OIDC)
7. Verify: `python3 -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('https://pypi.org/pypi/streamflacr/json').read())['info']['version'])"`
8. Clean install: `uv cache clean streamflacr && uv tool install streamflacr --force`

## Anti-Patterns (This Project)

- **NEVER** hardcode `/Users/<username>` paths — always use `Path.home()`
- **NEVER** put `serato-tools` in `pyproject.toml` dependencies (llvmlite build failure)
- **NEVER** modify Serato files without backing up first (via `backup.py` library backup)
- **NEVER** delete Serato data on uninstall — only remove StreamFLACr's own artifacts
- **NEVER** send OAuth + client_id together in SoundCloud API requests (causes 403)
- **NEVER** use `yt-dlp` for SoundCloud track fetching (triggers DRM protection)
- **DO NOT** kill parent shell process when cleaning up stale daemons — only match Python processes via `pgrep -f "python.*streamflacr"` and skip `os.getpid()` and `os.getppid()`
- **DO NOT** assume plist name is stable — handle both `com.djtchill.streamflacr` (legacy) and `com.streamflacr` (current)
- **DO NOT** start a duplicate instance when one is already running — use `is_running()` from `daemon.py` and tail the log file instead
- **DO NOT** delete or modify existing Serato crates or playlists without explicit permission and 3x confirmation

## Notes & Gotchas

- **Plist rename**: v0.12.1 changed plist from `com.djtchill.streamflacr` to `com.streamflacr`. Uninstall must check BOTH names. Setup must unload old plist if it exists.
- **Chrome PWA**: OAuth retry looks for `~/Applications/Chrome Apps.localized/SoundCloud.app` before falling back to full Chrome.
- **aioslsk port conflicts**: If ports 60000/60001 are occupied, `soulseek.py` continues without listening ports (download still works, upload won't).
- **SoundCloud DRM**: We only use API v2 for metadata (never yt-dlp). DRM errors should not occur.
- **CancelledError on shutdown**: Caught in `amain()` alongside KeyboardInterrupt for clean Ctrl+C.
- **aioslsk connection errors**: `PeerConnectionError` and `ConnectionFailedError` from aioslsk are normal P2P network chatter. Suppressed at CRITICAL level unless `--verbose`.
- **SoundCloud pagination**: API v2 only returns ~5-10 tracks per playlist inline. `fetch_playlist_tracks()` uses `/playlists/{id}?representation=full` + batch ID fetch to get all tracks.
- **SoundCloud rate limits**: ~600 requests per 10 minutes. We rate-limit to ~1 req/sec.
- **Smart crate matching**: Uses `Comment IS <playlist_name>` as the sole rule. The `description` Vorbis tag (FLAC) and `COMM` with empty description (MP3) are set to the playlist name. The `label`/`TPUB` field is the actual record label from SoundCloud, NOT used for crate matching.
- **Serato awareness**: `serato_watch.py` checks if Serato DJ is running. When active, downloaded files stay in staging; flushed to Auto Import only after Serato exits. Prevents half-tagged imports. Daemon checks every 30 seconds.
- **Artist resolution**: Uses `canonical_artist` (from `publisher_metadata.artist`) for Soulseek search, not `track.artist` (which is the SoundCloud handle like "heisrema").
- **Graceful shutdown**: `streamflacr stop` writes a flag file and sends SIGUSR1. The daemon checks `should_stop()` between operations and uses `asyncio.wait_for(_stop_event.wait(), timeout=poll_interval)` so SIGUSR1 wakes it from sleep immediately.
- **Single instance**: Running `streamflacr` when a daemon is already running tails the log file instead of starting a duplicate. `--force` overrides this.
- **Log file**: `~/.config/streamflacr/streamflacr.log` (rotating, 5MB max, 3 backups). Both console and file handlers are always active.
- **PID file**: `~/.config/streamflacr/streamflacr.pid` tracks the running daemon process. Stale PIDs are cleaned up automatically.
- **Primary DJ**: Configured via `PRIMARY_DJ` (serato or rekordbox). Determines which Auto Import folder to use.
- **Two-way sync**: `TWO_WAY_SYNC=1` enables syncing between Serato and Rekordbox libraries.
- **Playlist mode**: `PLAYLIST_MODE=all` monitors all playlists; `PLAYLIST_MODE=custom` + `MONITORED_PLAYLISTS` for specific playlists.
- **Library backups**: `BACKUP_ENABLED=1` with `BACKUP_SERATO=1` and/or `BACKUP_REKORDBOX=1`. Zips metadata only to `~/Music/LibraryBackups`. Max 10 backups.
- **Download verification**: Each download is verified via `fingerprint.py` using chromaprint + AcoustID (if available). Low-confidence matches are skipped and the next candidate is tried. The `state.json` tracks `verified`, `verification_method`, and `verification_confidence` per download.
- **fpcalc/chromaprint**: Optional but recommended. Install via `brew install chromaprint`. Without it, only metadata-based verification is used.
- **AcoustID**: Optional API key at https://acoustid.org/api-key. Enables ISRC-based definitive matching. Set `ACOUSTID_API_KEY` in `.env`.
- **Auto-update**: Checks PyPI on startup and every 4 hours (configurable via `AUTO_UPDATE_INTERVAL`). Writes an `auto-update-pending` flag and triggers graceful shutdown. The next launch runs `perform_pending_update()` which upgrades the package, migrates data, and restarts.
