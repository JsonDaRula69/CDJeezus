# StreamFLACr — Project Knowledge Base

**Last updated:** v0.25.0
**Stack:** Python 3.11+, macOS, aioslsk, mutagen, serato-tools, pydantic-settings

## Overview

StreamFLACr monitors SoundCloud playlists for new tracks, searches Soulseek for FLAC versions (falling back to 320kbps MP3), downloads them, tags metadata, and creates matching Serato smart crates. macOS-only (uses Chrome cookie decryption, osascript notifications, launchd).

## Structure

```
streamflacr/
├── __init__.py          # Version
├── __main__.py          # Daemon: poll loop, track processing, graceful shutdown via SIGUSR1
├── cli.py               # Argparse entry point, logging config, instance detection, stop/log commands
├── config.py            # Env-based config via .env in ~/.config/streamflacr/
├── daemon.py            # PID tracking, stop signaling (SIGUSR1 + flag file), single-instance, log tailing
├── fingerprint.py       # Audio fingerprinting via chromaprint/AcoustID for download verification
├── soundcloud.py        # API v2 with dual-attempt auth (OAuth first, client_id fallback)
├── soulseek.py           # Search/download via aioslsk; graceful port conflict handling
├── match.py              # Fuzzy matching: filename parsing, version descriptors, scoring
├── metadata.py           # FLAC (Vorbis) + MP3 (ID3v2) tagging; verify + enrich from SC data
├── serato_crate.py       # Smart crate: Comment IS <playlist_name> rule; backup before write
├── serato_watch.py       # Detect Serato running; flush staging → Auto Import on exit
├── notify.py             # macOS notifications via osascript
├── setup.py              # Interactive setup wizard, full_uninstall(), LaunchDaemon management
├── state.py              # JSON state file tracking seen tracks, download history, verification status
└── updater.py             # Self-update: stop daemon, upgrade, migrate data, restart
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
| Change backup rotation | `serato_crate.py` | `MAX_BACKUPS = 5` |
| Fix OAuth auth flow | `soundcloud.py` | `_get_user_id()` — Chrome launch + 3 retries |
| Fix setup wizard steps | `setup.py` | `run_setup()` — 4 steps (SoundCloud, Fingerprinting, Soulseek, Config) |
| Change state schema | `state.py` + `updater.py` | `STATE_VERSION`, `_migrate_state()` |
| Change fingerprint verification | `fingerprint.py` | `verify_download()`, `check_fpcalc()`, `lookup_acoustid()` |
| Change AcoustID config | `config.py` | `ACOUSTID_API_KEY`, `FINGERPRINT_VERIFY` |

## Architecture & Design Decisions

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
`_api_get()` tries OAuth header first (no `client_id` param), then falls back to `client_id`-only. Sending both together causes 403. OAuth token is decrypted from Chrome's SQLite cookie DB using macOS Keychain's "Chrome Safe Storage" key.

### OAuth Retry with Chrome Launch
When `_get_user_id()` fails (stale token or Chrome not running), it launches Chrome (or SoundCloud PWA if installed at `~/Applications/Chrome Apps.localized/SoundCloud.app`) and retries 3 times with 60s gaps. After all retries fail, sends macOS notification.

### SoundCloud Track Pagination
SoundCloud API v2 only returns ~5-10 tracks inline per playlist. `fetch_playlist_tracks()` resolves the playlist, then fetches `/playlists/{id}?representation=full` for complete data. If still incomplete, it extracts track IDs and batch-fetches via `/tracks?ids=...`.

### Non-Blocking SoundCloud Calls
All SoundCloud API calls are synchronous (using `requests`) and are wrapped in `asyncio.to_thread()` in the async callers to avoid blocking the aioslsk event loop. The `_rate_limit()` sleep only blocks the thread, not the event loop.

### Staging Directory for Metadata
Files download to `~/.config/streamflacr/staging/` (NOT inside `_Serato_`), get tagged with metadata, then are atomically moved (`os.replace`) to `_Serato_/Auto Import`. When Serato DJ is running, files stay in staging; they are flushed to Auto Import only when Serato exits (Serato only scans Auto Import on startup).

### Download Priority
FLAC (tier 0) > 320kbps MP3 (tier 1). Never below 320kbps. Files below `MIN_FILESIZE_MB` (5MB) are skipped.

### Matching: High Confidence vs Multi-Download
`HIGH_CONFIDENCE_SCORE = 0.70`: single download when top match >= 0.70. Multi-download (max 2 version groups) only for ambiguous cases below 0.70.

### serato-tools Dependency
`serato-tools` is NOT in `pyproject.toml` dependencies because it pulls in `librosa → numba → llvmlite` which fails to build. Instead, `serato_crate.py` auto-installs it with `--no-deps` at runtime.

### Serato Smart Crate
Uses `Comment IS <playlist_name>` as the sole rule. The `description` Vorbis tag (FLAC) and `COMM` with empty description (MP3) is set to the playlist name for matching. The `label`/`TPUB` field stores the actual record label from SoundCloud.

### Serato Data Sensitivity
Serato data is sacred. Never modify/delete anything in `~/Music/_Serato_` except `.scrate` files (backed up first). Staging is in `~/.config/streamflacr/staging`, not inside `_Serato_`.

### State File (state.json)
Version-tracked at `~/.config/streamflacr/state.json`. Current schema is v3:
- `version`: schema version (for migrations)
- `playlists`: map of playlist URL → `{name, seen_track_ids, downloaded}`
- `serato_blocked_transfer`: whether files are pending in staging because Serato is running

### Audio Fingerprinting & Verification

After downloading a file from Soulseek, StreamFLACr verifies it matches the expected SoundCloud track using three tiers:

1. **Tier 1 — Metadata only** (always available): Compares the downloaded file's own tags (artist, title) against SoundCloud metadata. Uses the same scoring algorithm as `match.py`.

2. **Tier 2 — Fingerprint duration** (requires `fpcalc`/chromaprint): Generates a chromaprint fingerprint and gets an accurate audio duration. Compares with SoundCloud's reported duration. Combined with metadata check for a more reliable score.

3. **Tier 3 — AcoustID lookup** (requires `fpcalc` + API key): Looks up the fingerprint on AcoustID's database. If the ISRC matches SoundCloud's ISRC, it's a definitive verification (confidence 1.0). If only title/artist match, it's high confidence (0.7-0.95).

If verification fails (confidence < 0.5), StreamFLACr skips the download and tries the next candidate. This prevents downloading the wrong version of a song — e.g., an original mix when the SoundCloud track is a remix.

**Custom mixes** that aren't in AcoustID will fall back to tiers 1-2. Uncertain matches are logged and the user is notified.

- `fpcalc` is detected during setup and in `fingerprint.py:check_fpcalc()`
- `ACOUSTID_API_KEY` is optional — without it, only tiers 1-2 are used
- `FINGERPRINT_VERIFY=1` (default) enables verification; set to `0` to disable
- Setup wizard step 2 checks for `fpcalc` and suggests `brew install chromaprint`

### Data Migration
When any code change modifies the format of `state.json`, config, or other operational data, a migration step must be added to `updater._migrate_state()`. Similarly, any code change requires evaluating whether the installer (`setup.py`) or uninstaller (`full_uninstall()`) need updates.

## Commands

```bash
streamflacr              # Run once (or attach to existing daemon)
streamflacr --daemon     # Run as persistent daemon
streamflacr --force      # Force start even if another instance is running
streamflacr stop         # Gracefully stop the daemon (complete downloads, flush staging)
streamflacr log          # Tail the daemon's log output
streamflacr setup        # Interactive setup wizard
streamflacr update       # Self-update with daemon restart
streamflacr update --check  # Check for updates only
streamflacr uninstall    # Interactive uninstall (asks about keeping migration data)
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

## Anti-Patterns (This Project)

- **NEVER** hardcode `/Users/<username>` paths — always use `Path.home()`
- **NEVER** put `serato-tools` in `pyproject.toml` dependencies (llvmlite build failure)
- **NEVER** modify Serato files without backing up first (`backup_serato_changes()`)
- **NEVER** delete Serato data on uninstall — only remove StreamFLACr's own artifacts
- **NEVER** send OAuth + client_id together in SoundCloud API requests (causes 403)
- **NEVER** use `yt-dlp` for SoundCloud track fetching (triggers DRM protection)
- **DO NOT** kill parent shell process when cleaning up stale daemons — only match Python processes via `pgrep -f "python.*streamflacr"` and skip `os.getpid()` and `os.getppid()`
- **DO NOT** assume plist name is stable — handle both `com.djtchill.streamflacr` (legacy) and `com.streamflacr` (current)
- **DO NOT** start a duplicate instance when one is already running — use `is_running()` from `daemon.py` and tail the log file instead

## Notes & Gotchas

- **Plist rename**: v0.12.1 changed plist from `com.djtchill.streamflacr` to `com.streamflacr`. Uninstall must check BOTH names. Setup must unload old plist if it exists.
- **Chrome PWA**: OAuth retry looks for `~/Applications/Chrome Apps.localized/SoundCloud.app` before falling back to full Chrome.
- **aioslsk port conflicts**: If ports 60000/60001 are occupied, `soulseek.py` continues without listening ports (download still works, upload won't).
- **SoundCloud DRM**: We only use API v2 for metadata (never yt-dlp). DRM errors should not occur.
- **CancelledError on shutdown**: Caught in `amain()` alongside KeyboardInterrupt for clean Ctrl+C.
- **aioslsk connection errors**: `PeerConnectionError` and `ConnectionFailedError` from aioslsk are normal P2P network chatter. Suppressed at CRITICAL level unless `--verbose`.
- **SoundCloud pagination**: API v2 only returns ~5-10 tracks per playlist inline. `fetch_playlist_tracks()` uses `/playlists/{id}?representation=full` + batch ID fetch to get all tracks.
- **SoundCloud rate limits**: ~600 requests per 10 minutes. We rate-limit to ~1 req/sec.
- **Smart crate matching**: Uses `Comment IS <playlist_name>` as the sole rule. The `comment` field in FLAC (Vorbis `description`) / MP3 (ID3v2 `COMM` with desc `""`) is set to the playlist name. The `label`/`TPUB` field is NOT used for crate matching.
- **Serato awareness**: `serato_watch.py` checks if Serato DJ is running. When active, downloaded files stay in staging; flushed to Auto Import only after Serato exits. Prevents half-tagged imports. Daemon checks every 30 seconds.
- **Artist resolution**: Uses `canonical_artist` (from `publisher_metadata.artist`) for Soulseek search, not `track.artist` (which is the SoundCloud handle like "heisrema").
- **Graceful shutdown**: `streamflacr stop` writes a flag file and sends SIGUSR1. The daemon checks `should_stop()` between operations and uses `asyncio.wait_for(_stop_event.wait(), timeout=poll_interval)` so SIGUSR1 wakes it from sleep immediately.
- **Single instance**: Running `streamflacr` when a daemon is already running tails the log file instead of starting a duplicate. `--force` overrides this.
- **Log file**: `~/.config/streamflacr/streamflacr.log` (rotating, 5MB max, 3 backups). Both console and file handlers are always active.
- **PID file**: `~/.config/streamflacr/streamflacr.pid` tracks the running daemon process. Stale PIDs are cleaned up automatically.
- **Download verification**: Each download is verified via `fingerprint.py` using chromaprint + AcoustID (if available). Low-confidence matches are skipped and the next candidate is tried. The `state.json` tracks `verified`, `verification_method`, and `verification_confidence` per download.
- **fpcalc/chromaprint**: Optional but recommended. Install via `brew install chromaprint`. Without it, only metadata-based verification is used.
- **AcoustID**: Optional API key at https://acoustid.org/api-key. Enables ISRC-based definitive matching. Set `ACOUSTID_API_KEY` in `.env`.
