# CDJeezus

*Because the way some DJs talk about Pioneer equipment, it's not a brand — it's a religion. This is the software that lets you take communion.*

Automatically monitors SoundCloud playlists, searches Soulseek for lossless versions, downloads them, tags metadata, creates Serato smart crates, and keeps your DJ library backed up.

## How it works

1. **Discovers** all your SoundCloud playlists automatically (including private ones)
2. **Monitors** for new tracks added to any playlist
3. **Searches** Soulseek for FLAC (or 320kbps MP3) versions of each track
4. **Verifies** downloads via audio fingerprinting (chromaprint/AcoustID) when available
5. **Tags** metadata: artist, title, comment (playlist name), label, ISRC, composer
6. **Creates** a Serato smart crate per playlist with rule: `Comment IS <playlist_name>`
7. **Backs up** your Serato/Rekordbox metadata before every session

## Install

```bash
pipx install cdjeezus
```

Or from source:

```bash
pipx install .
```

## Setup

```bash
cdjeezus setup
```

The 8-step setup wizard detects your DJ software, configures SoundCloud auth, Soulseek credentials, AcoustID, playlist selection, and library backups.

## Usage

```bash
cdjeezus              # Run once (or attach to running daemon)
cdjeezus --daemon     # Run as persistent daemon
cdjeezus stop         # Gracefully stop the daemon
cdjeezus log          # Tail daemon log output
cdjeezus setup        # Re-run setup wizard
cdjeezus update       # Self-update to latest version
cdjeezus update --check  # Check for updates only
cdjeezus uninstall    # Interactive uninstall
```

## Configuration

All config lives in `~/.config/cdjeezus/.env` (created by `cdjeezus setup`):

| Variable | Default | Description |
|---|---|---|
| `PRIMARY_DJ` | `serato` | Primary DJ software (serato or rekordbox) |
| `TWO_WAY_SYNC` | `0` | Enable 2-way sync between DJ libraries |
| `SLSK_USERNAME` | — | Soulseek username (required) |
| `SLSK_PASSWORD` | — | Soulseek password (required) |
| `SOUNDCLOUD_USER_URL` | auto | Your SoundCloud profile URL |
| `PLAYLIST_MODE` | `all` | `all` or `custom` |
| `MONITORED_PLAYLISTS` | — | Comma-separated playlist URLs (if custom) |
| `BACKUP_ENABLED` | `0` | Enable library backups |
| `BACKUP_SERATO` | `0` | Include Serato in backups |
| `BACKUP_REKORDBOX` | `0` | Include Rekordbox in backups |
| `ACOUSTID_API_KEY` | — | AcoustID API key for fingerprint verification |
| `AUTO_UPDATE_INTERVAL` | `14400` | Auto-update check interval in seconds (4 hours) |
| `SEARCH_TIMEOUT` | `30` | Seconds to wait for Soulseek results |
| `PREFER_FREE_SLOTS` | `1` | Prefer users with free upload slots |
| `MIN_FILESIZE_MB` | `5` | Skip files smaller than this |
| `FINGERPRINT_VERIFY` | `1` | Enable audio fingerprint verification |
| `UPSCALE_ENABLED` | `0` | Enable library upscaling (not yet implemented) |

## Migration from StreamFLACr

CDJeezus automatically migrates your existing StreamFLACr configuration:
- `~/.config/streamflacr/` → `~/.config/cdjeezus/`
- LaunchAgent plist is updated from `com.streamflacr` to `com.cdjeezus`
- All state, config, and history is preserved
