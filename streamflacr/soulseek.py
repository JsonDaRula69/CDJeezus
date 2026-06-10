"""Soulseek search and download via aioslsk."""

import asyncio
import logging
from pathlib import Path

from aioslsk.client import SoulSeekClient
from aioslsk.protocol.primitives import AttributeKey
from aioslsk.search.model import SearchRequest
from aioslsk.settings import Settings, CredentialsSettings, SharesSettings
from aioslsk.transfer.model import TransferState

from .config import (
    DOWNLOAD_DIR,
    MIN_FILESIZE_MB,
    PREFER_FREE_SLOTS,
    SLSK_PASSWORD,
    SLSK_USERNAME,
)

logger = logging.getLogger(__name__)

MIN_MP3_BITRATE = 320


class SoulseekDownloader:
    """Manages a persistent Soulseek connection for searching and downloading."""

    def __init__(self):
        self.client: SoulSeekClient | None = None
        self._download_dir = str(DOWNLOAD_DIR)

    async def connect(self) -> None:
        settings = Settings(
            credentials=CredentialsSettings(
                username=SLSK_USERNAME,
                password=SLSK_PASSWORD,
            ),
            shares=SharesSettings(
                download=self._download_dir,
                directories=[],
                scan_on_start=False,
            ),
        )
        self.client = SoulSeekClient(settings=settings)
        await self.client.start(connect=True)
        await self.client.login()
        logger.info("Logged into Soulseek as %s", SLSK_USERNAME)

    async def disconnect(self) -> None:
        if self.client:
            await self.client.stop()
            self.client = None

    async def search_track(
        self, artist: str, title: str, timeout: int = 30
    ) -> list[dict]:
        """Search for a track on Soulseek, prioritizing FLAC then 320kbps MP3.

        Returns candidates sorted by quality tier then preference:
          - Tier 0: FLAC (lossless, highest quality)
          - Tier 1: 320kbps MP3 (fallback, minimum acceptable quality)

        Each candidate dict has: username, filename, filesize, bitrate,
        has_free_slots, avg_speed, remote_path, tier.
        """
        if not self.client:
            raise RuntimeError("Not connected to Soulseek")

        query = f'"{artist}" "{title}"'
        logger.info("Searching Soulseek: %s", query)

        search_manager = self.client.search_manager
        request: SearchRequest = await search_manager.search(query)

        await asyncio.sleep(timeout)

        candidates = []
        for result in request.results:
            for item in result.shared_items:
                filename = item.filename
                lower = filename.lower()

                # Determine quality tier
                if lower.endswith(".flac"):
                    tier = 0
                elif lower.endswith(".mp3"):
                    attrs = item.get_attribute_map()
                    bitrate = attrs.get(AttributeKey.BITRATE, 0)
                    if bitrate < MIN_MP3_BITRATE:
                        logger.debug(
                            "Skipping %dkbps MP3 (below %dkbps minimum): %s",
                            bitrate, MIN_MP3_BITRATE, filename,
                        )
                        continue
                    tier = 1
                else:
                    # Skip non-audio and other formats
                    continue

                filesize_mb = item.filesize / (1024 * 1024)
                if filesize_mb < MIN_FILESIZE_MB:
                    continue

                attrs = item.get_attribute_map()
                bitrate = attrs.get(AttributeKey.BITRATE, 0)

                candidates.append({
                    "username": result.username,
                    "filename": filename,
                    "filesize": item.filesize,
                    "bitrate": bitrate,
                    "has_free_slots": result.has_free_slots,
                    "avg_speed": result.avg_speed,
                    "remote_path": filename,
                    "tier": tier,
                })

        # Sort: FLAC before MP3, then prefer free slots + fast speed + larger file
        def sort_key(c):
            slot_pref = 0 if (PREFER_FREE_SLOTS and c["has_free_slots"]) else 1
            return (c["tier"], slot_pref, -c["avg_speed"], -c["filesize"])

        candidates.sort(key=sort_key)

        flac_count = sum(1 for c in candidates if c["tier"] == 0)
        mp3_count = sum(1 for c in candidates if c["tier"] == 1)
        logger.info(
            "Found %d candidates for %s - %s (%d FLAC, %d 320kbps MP3)",
            len(candidates), artist, title, flac_count, mp3_count,
        )
        return candidates

    async def download(self, username: str, remote_path: str) -> Path | None:
        """Download a file from a Soulseek user. Returns the local path on success."""
        if not self.client:
            raise RuntimeError("Not connected to Soulseek")

        transfer_manager = self.client.transfer_manager
        logger.info("Requesting download: %s from %s", remote_path, username)

        transfer = await transfer_manager.download(username, remote_path)

        max_wait = 600
        poll_interval = 2
        waited = 0
        while waited < max_wait:
            await asyncio.sleep(poll_interval)
            waited += poll_interval

            state = transfer.state
            if isinstance(state, TransferState.Complete):
                local_path = transfer.local_path
                if local_path and Path(local_path).exists():
                    logger.info("Download complete: %s", local_path)
                    return Path(local_path)
                logger.warning("Transfer complete but file missing: %s", local_path)
                return None
            if isinstance(state, (TransferState.Failed, TransferState.Aborted)):
                logger.error("Transfer failed: %s", getattr(state, "fail_reason", "unknown"))
                return None

        logger.warning("Download timed out after %ds: %s", max_wait, remote_path)
        await transfer_manager.abort(transfer)
        return None
