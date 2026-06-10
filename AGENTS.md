# StreamFLACr — Project Knowledge Base

**Last updated:** v0.12.1
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
├── serato_crate.py       # Smart crate creation via serato-tools; backup before write
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
- **Git commit**: Use `[$omo:debugging]` skill before every commit to verify no code issues.

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
2. Commit with `v<version>` message
3. `git push origin main`
4. `gh release create v<version> --title "v<version>" --notes "..."`
5. GitHub Actions publishes to PyPI via trusted publishing (OIDC, no API tokens)
6. Verify on PyPI: `python3 -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('https://pypi.org/pypi/streamflacr/json').read())['info']['version'])"`
7. Clean install test: `uv cache clean streamflacr && uv tool install streamflacr --force`

## Notes & Gotchas

- **Plist rename**: v0.12.1 changed plist from `com.djtchill.streamflacr` to `com.streamflacr`. Uninstall must check for BOTH names. Setup must unload old plist if it exists.
- **Chrome PWA**: OAuth retry looks for `~/Applications/Chrome Apps.localized/SoundCloud.app` before falling back to full Chrome.
- **aioslsk port conflicts**: If ports 60000/60001 are occupied, `soulseek.py` continues without listening ports (download still works, upload won't).
- **SoundCloud DRM**: We only use API v2 for metadata (never yt-dlp). DRM errors should not occur.
- **CancelledError on shutdown**: Caught in `amain()` alongside KeyboardInterrupt for clean Ctrl+C.
