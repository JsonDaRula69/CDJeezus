# CDJeezus — Project Knowledge Base

**Last updated:** v0.30.0
**Stack:** Python 3.11+, macOS (primary), aioslsk, mutagen, serato-tools, rich, questionary

## Overview

CDJeezus monitors SoundCloud playlists for new tracks, searches Soulseek for
lossless versions (format priority: AIFF > WAV > FLAC > MP3 320kbps), downloads
them, converts to AIFF for CDJ compatibility, tags metadata, and creates matching
Serato smart crates. macOS-primary with planned Windows support.

The name says it all: "They said I can't bring my Numark, so I guess we're going
old school again."

## Structure

```
cdjeezus/
├── __init__.py          # Version
├── __main__.py          # Daemon: poll loop, track processing, graceful shutdown, auto-update
├── cli.py               # Argparse entry point, logging config, instance detection, stop/log
├── config.py            # Env-based config via .env in ~/.config/cdjeezus/
├── daemon.py            # PID tracking, stop signaling (SIGUSR1 + flag file), single-instance, log tailing
├── style.py             # rich + questionary TUI: banner, intro rant, menus, countdown, colors
├── converter.py         # FLAC/WAV -> AIFF conversion via ffmpeg for CDJ compatibility
├── fingerprint.py       # Audio fingerprinting via chromaprint/AcoustID for download verification
├── backup.py            # Library backup system (zip Serato/Rekordbox metadata to ~/Music/LibraryBackups)
├── library_scan.py      # Local library scanning and AcoustID fingerprint assignment
├── soundcloud.py        # API v2 with dual-attempt auth (OAuth first, client_id fallback)
├── soulseek.py          # Search/download via aioslsk; format tier priority (AIFF > WAV > FLAC > MP3)
├── match.py             # Fuzzy matching: filename parsing, version descriptors, scoring
├── metadata.py          # FLAC (Vorbis) + MP3 (ID3v2) + AIFF (ID3v2) tagging
├── serato_crate.py      # Smart crate: Comment IS <playlist_name> rule
├── serato_watch.py      # Detect Serato running; flush staging → Auto Import on exit
├── notify.py            # macOS notifications via osascript
├── setup.py             # Interactive setup wizard (8 steps), full_uninstall(), LaunchDaemon management
├── state.py             # JSON state file (v5 schema) tracking seen tracks, downloads, verification
└── updater.py           # Self-update: check PyPI, auto-update daemon, migrate data, CLI update command
```

## Where to Look

| Task | File | Key function |
|------|------|---------------|
| Add a new SoundCloud API endpoint | `soundcloud.py` | `_api_get()` |
| Change download quality/format priority | `soulseek.py` | `search_track()` — `FORMAT_TIERS` dict |
| Change format conversion behavior | `converter.py` | `convert_to_aiff()`, `needs_conversion()` |
| Change matching algorithm | `match.py` | `filter_and_rank_candidates()` — `HIGH_CONFIDENCE_SCORE = 0.70` |
| Change what metadata gets tagged | `metadata.py` | `tag_file()`, `enrich_metadata()` |
| Add a new audio format for tagging | `metadata.py` | `_tag_aiff()` / `_tag_flac()` / `_tag_mp3()` pattern |
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
| Change TUI style/colors | `style.py` | All rendering via `rich.Console`, prompts via `questionary` |
| Change menu styling | `setup.py` | All prompts via `style.py` helpers |

## Architecture & Design Decisions

### Format Priority (v0.30.0+)
- Search tier order: AIFF (0) > WAV (1) > FLAC (2) > MP3 320kbps (3)
- FLAC and WAV files are automatically converted to AIFF after download via `converter.py`
- AIFF is the target format for all Serato/CDJ playback (PCM, ID3v2 tags, universal CDJ support)
- MP3 files are NOT converted (they remain as-is since Serato handles them natively)
- Conversion uses ffmpeg with `-c:a pcm_s16be` (big-endian 16-bit PCM, standard AIFF)
- If ffmpeg is missing, FLAC/WAV files stay in their original format (still playable, just not AIFF)

### TUI Style System (v0.28.0+)
- `style.py` is the single source of truth for all terminal styling
- All output goes through `rich.Console` (cross-platform, Windows/macOS/Linux)
- All interactive prompts through `questionary` (arrow keys, space, enter)
- CDJ-themed palette: cyan (CDJ screen), amber accent, dim white secondary
- `NO_COLOR` env var is respected by rich automatically
- Intro rant typing animation falls back to simple print on non-interactive terminals

### Download Pipeline
```
SoundCloud API → metadata (artist, title, duration)
  → Soulseek search (AIFF > WAV > FLAC > MP3 tier)
  → Fuzzy match + version detection
  → Download to staging (~/.config/cdjeezus/staging/)
  → Convert FLAC/WAV to AIFF (if ffmpeg available)
  → Tag metadata (description = playlist name for smart crate)
  → Enrich metadata from SoundCloud (ISRC, composer, label)
  → Verify via AcoustID fingerprint (if fpcalc available)
  → Move to Auto Import (when Serato is NOT running)
```

### Serato Smart Crate Matching
- Smart crate rule: `Comment IS <playlist_name>` (sole rule)
- FLAC: `description` Vorbis tag = playlist name
- MP3: `COMM` with empty description = playlist name
- AIFF: `COMM` with empty description = playlist name
- The `label` / `TPUB` field = actual record label from SoundCloud (NOT used for crate matching)

### Key Rules

- **NEVER** hardcode `/Users/<username>` paths — use `Path.home()`
- **NEVER** put `serato-tools` in `pyproject.toml` dependencies (llvmlite build failure)
- **NEVER** delete music files, DJ libraries, or backups on uninstall
- **NEVER** delete ~/Music/LibraryBackups on uninstall
- **NEVER** send OAuth + client_id together in SoundCloud API requests (causes 403)
- **NEVER** use `yt-dlp` for SoundCloud track fetching (triggers DRM protection)
- **NEVER** modify existing Serato crates/playlists without explicit permission + 3x confirmation
- **DO NOT** add terminal styling outside of `style.py`
- **DO NOT** kill parent shell process when cleaning up stale daemons
- **DO NOT** start a duplicate instance when one is already running
- **DO NOT** assume plist name is stable — handle both `com.djtchill.cdjeezus` (legacy) and `com.cdjeezus` (current)

### Before Every Git Commit
1. Use `$omo:debugging` tool to check for code issues
2. Use `$omo:remove-ai-slops` tool to clean up the code
3. When state.json schema changes: add migration in `updater.py` AND `state.py`
4. Evaluate installer, uninstaller, and updater for any code changes that affect them

### Uninstaller Rules
- Interactive: only prompt is "Keep downloaded music files and migration data?" (default: Yes)
- Everything else is removed (config, plist, PID, state, daemon, serato-tools)
- NEVER delete ~/Music/LibraryBackups
- NEVER delete actual music files in _Serato_ or rekordbox directories

### Updater Rules
- `cdjeezus update`: fetches latest version, stops daemon, upgrades, migrates data, restarts
- When schema changes, add migration in `_migrate_state()` and `_migrate_env()`
- Always back up config before migration
- Evaluate installer/uninstaller for needed changes after any code modification

## Notes & Gotchas

- **Plist rename**: v0.12.1 changed plist from `com.djtchill.cdjeezus` to `com.cdjeezus`. Uninstall must check BOTH names. Setup must unload old plist if it exists.
- **Also check**: `com.streamflacr` and `com.djtchill.streamflacr` (legacy names from pre-renaming)
- **Chrome PWA**: OAuth retry looks for `~/Applications/Chrome Apps.localized/SoundCloud.app` before falling back to full Chrome.
- **aioslsk port conflicts**: If ports 60000/60001 are occupied, `soulseek.py` continues without listening ports (download still works, upload won't).
- **SoundCloud DRM**: We only use API v2 for metadata (never yt-dlp). DRM errors should not occur.
- **CancelledError on shutdown**: Caught in `amain()` alongside KeyboardInterrupt for clean Ctrl+C.
- **aioslsk connection errors**: `PeerConnectionError` and `ConnectionFailedError` from aioslsk are normal P2P network chatter. Suppressed at CRITICAL level unless `--verbose`.
- **SoundCloud pagination**: API v2 only returns ~5-10 tracks per playlist inline. `fetch_playlist_tracks()` uses `/playlists/{id}?representation=full` + batch ID fetch to get all tracks.
- **SoundCloud rate limits**: ~600 requests per 10 minutes. We rate-limit to ~1 req/sec.
- **SoundCloud retry delays**: 15s, 20s, 25s (with countdown display)
- **Artist resolution**: Uses `canonical_artist` (from `publisher_metadata.artist`) for Soulseek search, not `track.artist` (which is the SoundCloud handle like "heisrema").
- **Serato awareness**: `serato_watch.py` checks if Serato DJ is running. When active, downloaded files stay in staging; flushed to Auto Import only after Serato exits. Prevents half-tagged imports. Daemon checks every 30 seconds.
- **Staging directory**: `~/.config/cdjeezus/staging/` — NOT inside `_Serato_` folder (minimizes external activity in sensitive Serato directory).
- **Graceful shutdown**: `cdjeezus stop` writes a flag file and sends SIGUSR1. The daemon checks `should_stop()` between operations and uses `asyncio.wait_for(_stop_event.wait(), timeout=poll_interval)` so SIGUSR1 wakes it from sleep immediately.
- **Single instance**: Running `cdjeezus` when a daemon is already running tails the log file instead of starting a duplicate. `--force` overrides this.
- **Log file**: `~/.config/cdjeezus/cdjeezus.log` (rotating, 5MB max, 3 backups).
- **PID file**: `~/.config/cdjeezus/cdjeezus.pid`
- **ffmpeg**: System dependency, not Python package. Install via `brew install ffmpeg`. Required for FLAC→AIFF conversion.
- **fpcalc/chromaprint**: Optional but recommended. Install via `brew install chromaprint`. Without it, only metadata-based verification is used.
- **AcoustID**: Optional API key at https://acoustid.org/api-key. Enables ISRC-based definitive matching. Set `ACOUSTID_API_KEY` in `.env`.
- **Auto-update**: Checks PyPI on startup and every 4 hours. Writes an `auto-update-pending` flag and triggers graceful shutdown. The next launch runs `perform_pending_update()`.
- **PyPI publishing**: Uses GitHub Actions with dual auth: API token for first publish (new project), trusted publishing for subsequent releases. Add `PYPI_API_TOKEN` secret to repo for first release.
- **Branch protection**: All changes require PR + approval from `jsondarula` (owner can push directly to main).
