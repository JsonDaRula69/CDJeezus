"""Audio fingerprinting and post-download verification.

Verifies that files downloaded from Soulseek match the expected SoundCloud
track using chromaprint fingerprints, AcoustID database lookups (optional),
and embedded metadata comparison.

Three verification tiers:
  1. Embedded metadata + duration (always available)
  2. Chromaprint fingerprint + duration (requires fpcalc)
  3. AcoustID lookup (requires fpcalc + API key)

Tier 1 compares the file's own tags and duration against SoundCloud data.
Tier 2 uses chromaprint to get an accurate duration from the actual audio.
Tier 3 looks up the fingerprint on AcoustID and compares ISRC/title/artist.

For custom mixes or tracks not in AcoustID, we fall back to tier 1-2 and
flag the match as uncertain, notifying the user for manual review.
"""

import json
import logging
import os
import time
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import ACOUSTID_API_KEY
from .match import (
    extract_versions,
    jaccard_similarity,
    normalize,
    tokenize,
    version_match_score,
)

logger = logging.getLogger(__name__)

# How close the duration needs to be for a "match" (seconds)
DURATION_TOLERANCE_S = 3.0
# Minimum confidence to consider a verification "passing"
VERIFICATION_THRESHOLD = 0.70

# AcoustID rate limit: max 3 requests per second
_acoustid_last_request: float = 0.0
_acoustid_min_interval: float = 0.34  # ~3 req/s


def _acoustid_rate_limit() -> None:
    """Ensure we don't exceed AcoustID's 3 requests/second limit."""
    global _acoustid_last_request
    now = time.monotonic()
    elapsed = now - _acoustid_last_request
    if elapsed < _acoustid_min_interval:
        time.sleep(_acoustid_min_interval - elapsed)
    _acoustid_last_request = time.monotonic()


@dataclass
class VerificationResult:
    """Result of fingerprint-based verification."""
    verified: bool
    confidence: float  # 0.0 to 1.0
    method: str  # "isrc_match", "acoustid_metadata", "fingerprint_duration", "metadata_only", "unavailable"
    acoustid_isrc: str | None = None
    acoustid_title: str | None = None
    acoustid_artist: str | None = None
    file_duration_s: float | None = None
    notes: str = ""


def check_fpcalc() -> bool:
    """Check if fpcalc (chromaprint) is available on the system."""
    try:
        result = subprocess.run(
            ["fpcalc", "-version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def generate_fingerprint(filepath: Path) -> dict | None:
    """Generate a chromaprint fingerprint for an audio file.

    Returns dict with 'fingerprint', 'duration' keys, or None on failure.
    """
    try:
        result = subprocess.run(
            ["fpcalc", "-json", str(filepath)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.debug("fpcalc failed for %s: %s", filepath.name, result.stderr)
            return None

        data = json.loads(result.stdout)
        return {
            "fingerprint": data.get("fingerprint", ""),
            "duration": float(data.get("duration", 0)),
        }
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        logger.debug("Fingerprint generation failed for %s: %s", filepath.name, e)
        return None


def lookup_acoustid(fingerprint: str, duration: float) -> list[dict]:
    """Look up a fingerprint in the AcoustID database.

    Returns a list of result dicts, each with 'score' and 'recordings'.
    Each recording has 'id', 'title', 'artists', 'isrcs', 'duration'.
    """
    if not ACOUSTID_API_KEY:
        logger.debug("No AcoustID API key configured, skipping lookup")
        return []

    _acoustid_rate_limit()

    try:
        response = requests.get(
            "https://api.acoustid.org/v2/lookup",
            params={
                "client": ACOUSTID_API_KEY,
                "meta": "recordings+isrcs+tracks+releasegroups",
                "fingerprint": fingerprint,
                "duration": int(duration),
            },
            timeout=15,
        )
        data = response.json()
        if data.get("status") != "ok":
            logger.debug("AcoustID lookup failed: %s", data)
            return []
        return data.get("results", [])
    except Exception as e:
        logger.debug("AcoustID lookup error: %s", e)
        return []


def verify_embedded_metadata(
    filepath: Path,
    sc_artist: str,
    sc_title: str,
    sc_duration_s: float | None = None,
) -> float:
    """Compare a file's own metadata tags against SoundCloud info.

    Reads artist/title tags from the downloaded file and computes a
    similarity score. Returns 0.0 if tags are missing or don't match.
    """
    from .metadata import verify_metadata

    existing = verify_metadata(filepath)
    if not existing:
        return 0.0

    file_artist = existing.get("artist", "")
    file_title = existing.get("title", "")

    if not file_artist and not file_title:
        return 0.0

    # Compare artist
    artist_score = 0.0
    if file_artist:
        artist_score = jaccard_similarity(
            tokenize(normalize(file_artist)),
            tokenize(normalize(sc_artist)),
        )

    # Compare title (including version descriptors)
    title_score = 0.0
    if file_title:
        title_score = jaccard_similarity(
            tokenize(normalize(file_title)),
            tokenize(normalize(sc_title)),
        )
        # Check version match
        sc_versions = extract_versions(sc_title)
        file_versions = extract_versions(file_title)
        ver_score = version_match_score(sc_versions, file_versions)
        # Weight version matching heavily — wrong version = wrong track
        if ver_score < 0.3:
            title_score *= 0.5

    # Duration check (from file metadata, not as accurate as fpcalc)
    duration_score = 0.0
    file_duration_str = existing.get("duration") or existing.get("length")
    if sc_duration_s and file_duration_str:
        try:
            file_dur = float(file_duration_str)
            diff = abs(sc_duration_s - file_dur)
            if diff <= DURATION_TOLERANCE_S:
                duration_score = 1.0 - (diff / DURATION_TOLERANCE_S)
        except (ValueError, TypeError):
            pass

    weights = {"artist": 0.30, "title": 0.45, "duration": 0.25}
    score = (
        weights["artist"] * artist_score
        + weights["title"] * title_score
        + weights["duration"] * duration_score
    )
    return score


def verify_download(
    filepath: Path,
    sc_artist: str,
    sc_title: str,
    sc_duration_s: float | None = None,
    sc_isrc: str | None = None,
) -> VerificationResult:
    """Verify that a downloaded file matches the expected SoundCloud track.

    Uses a tiered approach:
      Tier 3: AcoustID lookup (fpcalc + API key)
        - ISRC match = definitive (1.0)
        - Title/artist match = high confidence (0.7-0.95)
      Tier 2: Fingerprint duration (fpcalc, no API key)
        - Duration match within tolerance = moderate confidence (0.6-0.8)
      Tier 1: Embedded metadata (always available)
        - Tag similarity = baseline confidence (0.0-0.7)

    Returns a VerificationResult with the best available evidence.
    """
    # ── Tier 2/3: Chromaprint fingerprint ────────────────────────────
    fp_data = generate_fingerprint(filepath)
    fp_duration = None
    if fp_data:
        fp_duration = fp_data["duration"]

    # ── Tier 3: AcoustID lookup (if we have fingerprint + API key) ───
    if fp_data and ACOUSTID_API_KEY:
        results = lookup_acoustid(fp_data["fingerprint"], fp_data["duration"])

        for result in results:
            recordings = result.get("recordings", [])
            for recording in recordings:
                # ISRC match = definitive
                isrcs = recording.get("isrcs", [])
                if sc_isrc and sc_isrc in isrcs:
                    ac_artist = ", ".join(
                        a.get("name", "") for a in recording.get("artists", [])
                    )
                    return VerificationResult(
                        verified=True,
                        confidence=1.0,
                        method="isrc_match",
                        acoustid_isrc=sc_isrc,
                        acoustid_title=recording.get("title"),
                        acoustid_artist=ac_artist,
                        file_duration_s=fp_duration,
                        notes="ISRC confirmed same recording",
                    )

                # Title/artist match from AcoustID
                ac_title = recording.get("title", "")
                ac_artists = [
                    a.get("name", "") for a in recording.get("artists", [])
                ]
                ac_artist = ", ".join(ac_artists)

                if ac_title and ac_artists:
                    title_sim = jaccard_similarity(
                        tokenize(normalize(sc_title)),
                        tokenize(normalize(ac_title)),
                    )
                    artist_sim = jaccard_similarity(
                        tokenize(normalize(sc_artist)),
                        tokenize(normalize(ac_artist)),
                    )
                    sc_versions = extract_versions(sc_title)
                    ac_versions = extract_versions(ac_title)
                    ver_score = version_match_score(sc_versions, ac_versions)

                    combined = (
                        title_sim * 0.4 + artist_sim * 0.35 + ver_score * 0.25
                    )
                    if combined >= 0.70:
                        found_isrc = isrcs[0] if isrcs else None
                        return VerificationResult(
                            verified=True,
                            confidence=combined,
                            method="acoustid_metadata",
                            acoustid_isrc=found_isrc,
                            acoustid_title=ac_title,
                            acoustid_artist=ac_artist,
                            file_duration_s=fp_duration,
                            notes=f"AcoustID match (score {combined:.2f})",
                        )

    # ── Tier 2: Fingerprint duration (fpcalc available, no AcoustID) ─
    if fp_duration and sc_duration_s:
        diff = abs(sc_duration_s - fp_duration)
        if diff <= DURATION_TOLERANCE_S:
            dur_confidence = 1.0 - (diff / DURATION_TOLERANCE_S) * 0.5
            # Combine with metadata check
            meta_score = verify_embedded_metadata(
                filepath, sc_artist, sc_title, sc_duration_s
            )
            combined = max(dur_confidence * 0.6 + meta_score * 0.4, meta_score)
            return VerificationResult(
                verified=combined >= VERIFICATION_THRESHOLD,
                confidence=combined,
                method="fingerprint_duration",
                file_duration_s=fp_duration,
                notes=f"Duration match ({diff:.1f}s diff) + metadata",
            )
        elif diff <= sc_duration_s * 0.1:
            # Within 10% — different version likely
            return VerificationResult(
                verified=False,
                confidence=0.3,
                method="fingerprint_duration",
                file_duration_s=fp_duration,
                notes=f"Duration mismatch ({diff:.1f}s diff) — possible different version",
            )

    # ── Tier 1: Embedded metadata only ───────────────────────────────
    meta_score = verify_embedded_metadata(
        filepath, sc_artist, sc_title, sc_duration_s
    )
    return VerificationResult(
        verified=meta_score >= VERIFICATION_THRESHOLD,
        confidence=meta_score,
        method="metadata_only",
        file_duration_s=fp_duration,
        notes="No fpcalc/AcoustID; metadata-only verification",
    )
