# StreamFLACr — Project Knowledge Base

**Last updated:** v0.19.1
**Stack:** Python 3.11+, macOS, aioslsk, mutagen, serato-tools, pydantic-settings

## Overview

StreamFLACr monitors SoundCloud playlists for new tracks, searches Soulseek for FLAC versions (falling back to 320kbps MP3), downloads them, tags metadata, and creates matching Serato smart crates. macOS-only (uses Chrome cookie decryption, osascript notifications, launchd).

## Structure

```
streamflacr/
├── __init__.py          # Version
├── __main__.py          # Daemon: poll loop, track processing, multi-version download logic
├── cli.py               # Argparse entry point, logging config, aioslsk noise suppression
├── config.py            # Env-based config via .env in ~/.config/streamflacr/
├── soundcloud.py        # API v2 with dual-attempt auth (OAuth first, client_id fallback)
├── soulseek.py           # Search/download via aioslsk; graceful port conflict handling
├── match.py              # Fuzzy matching: filename parsing, version descriptors, scoring
├── metadata.py           # FLAC (Vorbis) + MP3 (ID3v2) tagging; verify + enrich from SC data
├── serato_crate.py       # Smart crate: Comment IS <playlist_name> rule; backup before write
├── serato_watch.py       # Detect Serato running; flush staging → Auto Import on exit
├── notify.py             # macOS notifications via osascript
├── setup.py              # Interactive setup wizard, full_uninstall(), LaunchDaemon management
└── state.py              # JSON state file tracking seen tracks and download history
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
| Add CLI flags | `cli.py` | `main()` — argparse |
| Change daemon poll interval | `config.py` | `SOUNDCLOUD_POLL_INTERVAL` (default 300s) |
| Change backup rotation | `serato_crate.py` | `MAX_BACKUPS = 5` |
| Fix OAuth auth flow | `soundcloud.py` | `_get_user_id()` — Chrome launch + 3 retries |
| Fix setup wizard steps | `setup.py` | `run_setup()` — 3 steps (SoundCloud, Soulseek, Config) |

## Architecture & Design Decisions

### SoundCloud Auth (dual-attempt)
`_api_get()` tries OAuth header first (no `client_id` param), then falls back to `client_id`-only. Sending both together causes 403. OAuth token is decrypted from Chrome's SQLite cookie DB using macOS Keychain's "Chrome Safe Storage" key.

### OAuth Retry with Chrome Launch
When `_get_user_id()` fails (stale token or Chrome not running), it launches Chrome (or SoundCloud PWA if installed at `~/Applications/Chrome Apps.localized/SoundCloud.app`) and retries 3 times with 60s gaps. After all retries fail, sends macOS notification. The 60s `time.sleep()` is blocking but acceptable for a daemon that polls every 5 minutes.

### SoundCloud Track Pagination
SoundCloud API v2 only returns ~5-10 tracks inline per playlist. `fetch_playlist_tracks()` resolves the playlist, then fetches `/playlists/{id}?representation=full` for complete data. If still incomplete, it extracts track IDs and batch-fetches via `/tracks?ids=...`. `discover_user_playlists()` uses `linked_partitioning` with `next_href` to handle users with more than 50 playlists.

### Non-Blocking SoundCloud Calls
All SoundCloud API calls are synchronous (using `requests`) and are wrapped in `asyncio.to_thread()` in the async callers (`sync_playlist`, `poll_loop`, `run_once`) to avoid blocking the aioslsk event loop. The `_rate_limit()` sleep only blocks the thread, not the event loop.

### Staging Directory for Metadata
Files download to `~/.config/streamflacr/staging/` (NOT inside `_Serato_`), get tagged with metadata, then are atomically moved (`os.replace`) to `_Serato_/Auto Import`. When Serato DJ is running, files stay in staging; they are flushed to Auto Import only when Serato exits (Serato only scans Auto Import on startup).

### Download Priority
FLAC (tier 0) > 320kbps MP3 (tier 1). Never below 320kbps. Files below `MIN_FILESIZE_MB` (5MB) are skipped.

### Matching: High Confidence vs Multi-Download
`HIGH_CONFIDENCE_SCORE = 0.70`: single download when top match >= 0.70. Multi-download (max 2 version groups) only for ambiguous cases below 0.70. Version descriptors (Remix, Radio Edit, etc.) are extracted from filenames to group candidates.

### serato-tools Dependency
`serato-tools` is NOT in `pyproject.toml` dependencies because it pulls in `librosa → numba → llvmlite` which fails to build on most systems. We only use `smart_crate.py` which has zero librosa dependency. Instead, `serato_crate.py` auto-installs `serato-tools` with `--no-deps` on first import via `_ensure_serato_tools()`.

### LaunchAgent Management
Plist identifier: `com.streamflacr`. Location: `~/Library/LaunchAgents/`. `KeepAlive=true` means launchd respawns the daemon if killed. `kill_running_daemon()` calls `launchctl unload` first to prevent respawn, then kills stale Python processes.

### Serato Data is Sacred
Never delete or modify anything in `~/Music/_Serato_` except `.scrate` writes (backed up first). Uninstall must not touch it. Backups go to `~/Music/_Serato_Backup_SFr/Bk<timestamp>/` with 5-most-recent rotation.

### Config Resolution
All paths use `Path.home()`. Config file: `~/.config/streamflacr/.env`. Env vars override .env defaults. The plist also sets `STREAMFLACR_CONFIG_DIR`.

## Conventions

- **Version** is in `__init__.__version__` AND `pyproject.toml` — both must match on release.
- **Logging**: `streamflacr` logger for app-level events. aioslsk loggers are suppressed to ERROR/CRITICAL unless `--verbose`.
- **macOS notifications** via `osascript display notification` — no third-party deps.
- **Non-interactive paths**: All user-facing `input()` calls should have non-interactive fallbacks for CI/testing. Currently setup wizard is fully interactive; `streamflacr setup` is required before first run.
- **Git commit**: Use `[$omo:debugging]` skill before every commit to verify no code issues. Use `[$omo:remove-ai-slops]` skill to clean up the code before every commit.

## Anti-Patterns (This Project)

- **NEVER** hardcode `/Users/<username>` paths — always use `Path.home()`
- **NEVER** put `serato-tools` in `pyproject.toml` dependencies (llvmlite build failure)
- **NEVER** modify Serato files without backing up first (`backup_serato_changes()`)
- **NEVER** delete Serato data on uninstall — only remove StreamFLACr's own artifacts
- **NEVER** send OAuth + client_id together in SoundCloud API requests (causes 403)
- **NEVER** use `yt-dlp` for SoundCloud track fetching (triggers DRM protection)
- **DO NOT** kill parent shell process when cleaning up stale daemons — only match Python processes via `pgrep -f "python.*streamflacr"` and skip `os.getpid()` and `os.getppid()`
- **DO NOT** assume plist name is stable — handle both `com.djtchill.streamflacr` (legacy) and `com.streamflacr` (current)

## Commands

```bash
# Install (production)
uv tool install streamflacr --force

# Install (development)
pip install -e .

# Run once
streamflacr

# Run as daemon
streamflacr --daemon

# Setup wizard
streamflacr setup

# Uninstall
streamflacr uninstall

# Version
streamflacr --version
```

## Release Process

1. Bump version in `__init__.py` AND `pyproject.toml`
2. Use `[$omo:debugging]` and `[$omo:remove-ai-slops]` skills before committing
3. Commit with `v<version>` message
4. `git push origin main`
5. `gh release create v<version> --title "v<version>" --notes "..."`
6. GitHub Actions publishes to PyPI via trusted publishing (OIDC, no API tokens)
7. Verify on PyPI: `python3 -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('https://pypi.org/pypi/streamflacr/json').read())['info']['version'])"`
8. Clean install test: `uv cache clean streamflacr && uv tool install streamflacr --force`

## Notes & Gotchas

- **Plist rename**: v0.12.1 changed plist from `com.djtchill.streamflacr` to `com.streamflacr`. Uninstall must check for BOTH names. Setup must unload old plist if it exists.
- **Chrome PWA**: OAuth retry looks for `~/Applications/Chrome Apps.localized/SoundCloud.app` before falling back to full Chrome.
- **aioslsk port conflicts**: If ports 60000/60001 are occupied, `soulseek.py` continues without listening ports (download still works, upload won't).
- **SoundCloud DRM**: We only use API v2 for metadata (never yt-dlp). DRM errors should not occur.
- **CancelledError on shutdown**: Caught in `amain()` alongside KeyboardInterrupt for clean Ctrl+C.
- **aioslsk connection errors**: `PeerConnectionError` and `ConnectionFailedError` from aioslsk are normal P2P network chatter. Suppressed at CRITICAL level unless `--verbose`.
- **SoundCloud pagination**: API v2 only returns ~5-10 tracks per playlist inline. `fetch_playlist_tracks()` uses `/playlists/{id}?representation=full` + batch ID fetch to get all tracks.
- **SoundCloud rate limits**: ~600 requests per 10 minutes. We rate-limit to ~1 req/sec.

- **Smart crate matching**: Uses `Comment IS <playlist_name>` as the sole rule. The `comment` field in FLAC (Vorbis) / MP3 (ID3v2 `COMM` with desc `StreamFLACr`) is set to the playlist name. The `label`/`TPUB` field is NOT used for crate matching — it was migrated away because label is actual metadata about the song's label/record company, not about the playlist.
- **Serato awareness**: `serato_watch.py` checks if Serato DJ is running. When active, downloaded files stay in staging; they are flushed to Auto Import only after Serato exits. This prevents half-tagged imports and ensures Serato picks them up on next launch. The daemon checks every 30 seconds.
- **Artist resolution**: Uses `canonical_artist` (from `publisher_metadata.artist`) for Soulseek search, not `track.artist` (which is the SoundCloud handle like "heisrema").

- **`streamflacr update`**: Self-update command that stops the daemon, upgrades the package via `uv tool install --force --reinstall`, migrates data, and restarts. Preserves config/state between versions.
- **`streamflacr uninstall`**: Interactive — asks whether to keep downloaded music files and migration data. Everything else (config, logs, LaunchAgent, tool installation) is removed. Never touches Serato data.
- **Data migration**: When any code change modifies the format of `state.json`, config, or other operational data, a migration step must be added to `updater._migrate_state()`. Similarly, any code change requires evaluating whether the installer (`setup.py`) or uninstaller (`full_uninstall()`) need updates for fresh install or complete cleanup respectively.
- **Serato Comment field**: Serato DJ reads the Vorbis `description` tag (FLAC) and `COMM` with empty description (MP3) for its Comment column. We write the playlist name to both `description` and `comment` for FLAC, and `COMM(desc="")` for MP3. Smart crate rule: `Comment IS <playlist_name>`.
