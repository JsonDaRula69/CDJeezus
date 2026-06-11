# CDJeez

*They said I can't bring my Numark, so I guess we're going old school again.*

Automatically monitors SoundCloud playlists, searches Soulseek for lossless versions (AIFF > WAV > FLAC > MP3 320kbps), downloads them, converts to AIFF for CDJ compatibility, tags metadata, creates Serato smart crates, and keeps your DJ library backed up.

## How it works

1. **Discovers** all your SoundCloud playlists automatically (including private ones)
2. **Monitors** for new tracks added to any playlist
3. **Searches** Soulseek for lossless versions (AIFF > WAV > FLAC > MP3 320kbps)
4. **Converts** FLAC/WAV to AIFF for maximum CDJ and Serato compatibility
5. **Tags** metadata: artist, title, comment (playlist name), label, ISRC, composer
6. **Creates** a Serato smart crate per playlist with rule: `Comment IS <playlist_name>`
7. **Backs up** your Serato/Rekordbox metadata before every session

## Install

```bash
pipx install cdjeez
```

Or from source:

```bash
pipx install .
```

## Setup

```bash
cdjeez setup
```

The 8-step setup wizard detects your DJ software, configures SoundCloud auth, Soulseek credentials, AcoustID, playlist selection, and library backups.

## Usage

```bash
cdjeez              # Run once (or attach to running daemon)
cdjeez --daemon     # Run as persistent daemon
cdjeez stop         # Gracefully stop the daemon
cdjeez log          # Tail daemon log output
cdjeez setup        # Re-run setup wizard
cdjeez update       # Self-update to latest version
cdjeez update --check  # Check for updates only
cdjeez uninstall    # Interactive uninstall
```

## Configuration

All config lives in `~/.config/cdjeez/.env` (created by `cdjeez setup`):

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
