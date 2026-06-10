"""Fuzzy matching between SoundCloud tracks and Soulseek search results.

Handles the messy reality of Soulseek filenames: track numbers, varying
artist separators, "(Dirty)" / "(Clean)" suffixes, featured artists,
remix labels, and other noise.

Matching strategy:
  1. Extract artist + title from the Soulseek filename
  2. Normalize both sides for comparison
  3. Compare version descriptors (Remix, Edit, etc.) -- mismatches penalized
  4. Compute a weighted match score using artist, title, and duration
  5. Reject candidates below the minimum score threshold
"""

import logging
import re

from aioslsk.protocol.primitives import AttributeKey

logger = logging.getLogger(__name__)

MIN_MATCH_SCORE = 0.45
DURATION_TOLERANCE_S = 15

# Version descriptors that indicate a specific mix/version of a track
VERSION_PATTERN = re.compile(
    r"[\(\[\{]?\s*("
    r"(?:FLY|O.G\.|OG|DJ|Acapella|Instrumental|Extended|Radio|Club|Clean|Dirty|Explicit"
    r"|Original\s+Mix|Remix|Edit|Mix|Version|Demo|Live|Acoustic|Bonus|Snippet"
    r"|Intro|Outro|Interlude|Reprise|Mashup|VIP|Dub|Vocal\s+Mix|Sped\s+Up"
    r"|Slowed|Chopped|Remastered|Remaster)"
    r")\s*[\)\]\}]?",
    re.IGNORECASE,
)


def parse_soulseek_filename(filename: str) -> tuple[str, str]:
    """Extract artist and title from a Soulseek filename.

    Handles: "05 - ARTIST - Title.flac", "32. Artist - Title.flac",
    "01 Title.flac", "Artist - Title (Remix).flac", etc.
    """
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = re.sub(r"^\d{1,3}[\s.\-]+", "", name)
    parts = re.split(r"\s+-\s+|\s+-|--", name, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    parts = name.rsplit(",", 1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip() and len(parts[1].strip()) > 3:
        return parts[0].strip(), parts[1].strip()
    return "", name.strip()


def normalize(text: str) -> str:
    """Normalize text for fuzzy comparison."""
    text = text.lower()
    # Strip non-version parenthetical noise (Dirty, Clean, Explicit, Radio Edit)
    text = re.sub(r"[\(\[]?(?:dirty|clean|explicit|radio\s*edit)\s*[\)\]]?", "", text, flags=re.IGNORECASE)
    # Strip feat/ft/featuring (keep the primary artist)
    text = re.sub(r"\b(?:feat\.?|ft\.?|featuring)\b", "", text, flags=re.IGNORECASE)
    # Remove punctuation (but keep alphanumerics for version matching)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_versions(title: str) -> set[str]:
    """Extract version descriptors from a title string.

    Returns a set of lowercase version tags like {'remix', 'fly', 'extended mix'}.
    """
    versions = set()
    for m in VERSION_PATTERN.finditer(title):
        tag = m.group(1).strip().lower()
        if tag:
            versions.add(tag)
    return versions


def version_match_score(sc_versions: set[str], slsk_versions: set[str]) -> float:
    """Compare version descriptors between SoundCloud and Soulseek.

    Returns:
      1.0 -- versions match exactly or both are empty
      0.5 -- partial overlap (some versions match)
      0.0 -- conflicting versions (e.g. "remix" vs "original mix")
    """
    if not sc_versions and not slsk_versions:
        return 1.0  # neither has version info, no penalty
    if not sc_versions or not slsk_versions:
        return 0.7  # one has version info the other doesn't, slight penalty

    overlap = sc_versions & slsk_versions
    if overlap == sc_versions == slsk_versions:
        return 1.0  # exact match
    if overlap:
        return 0.5  # partial match

    # No overlap at all -- check for conflicts
    # "original mix" / "original" conflicts with "remix" / "edit"
    original_words = {"original mix", "original", "o.g.", "og"}
    remix_words = {"remix", "edit", "mix", "club mix", "club edit", "extended mix", "extended", "dub", "vip", "mashup"}

    sc_has_original = bool(sc_versions & original_words)
    sc_has_remix = bool(sc_versions & remix_words)
    slsk_has_original = bool(slsk_versions & original_words)
    slsk_has_remix = bool(slsk_versions & remix_words)

    # If one says "original" and other says "remix", that's a hard conflict
    if (sc_has_original and slsk_has_remix) or (sc_has_remix and slsk_has_original):
        return 0.0

    # Otherwise, no overlap but no hard conflict -- moderate penalty
    return 0.3


def tokenize(text: str) -> set[str]:
    return set(text.split()) if text else set()


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union)


def compute_match_score(
    sc_artist: str,
    sc_title: str,
    sc_duration_s: float | None,
    slsk_filename: str,
    slsk_duration_s: float | None,
) -> float:
    """Compute a match score (0-1) between a SoundCloud track and a Soulseek result.

    Weights:
      - Artist match: 0.30
      - Title match: 0.35
      - Version match: 0.20
      - Duration match: 0.15
    """
    slsk_artist, slsk_title = parse_soulseek_filename(slsk_filename)

    # Artist similarity
    sc_artist_norm = normalize(sc_artist)
    slsk_artist_norm = normalize(slsk_artist)
    sc_artist_tokens = tokenize(sc_artist_norm)
    slsk_artist_tokens = tokenize(slsk_artist_norm)
    artist_score = jaccard_similarity(sc_artist_tokens, slsk_artist_tokens)

    # Check if primary artist appears in the other side's artist string
    if slsk_artist_norm and sc_artist_norm:
        primary_sc = sc_artist_norm.split(",")[0].split("&")[0].strip()
        primary_slsk = slsk_artist_norm.split(",")[0].split("&")[0].strip()
        if primary_sc in slsk_artist_norm or primary_slsk in sc_artist_norm:
            artist_score = max(artist_score, 0.6)

    # Title similarity (core title, before version matching)
    sc_title_norm = normalize(sc_title)
    slsk_title_norm = normalize(slsk_title)
    sc_title_tokens = tokenize(sc_title_norm)
    slsk_title_tokens = tokenize(slsk_title_norm)
    title_score = jaccard_similarity(sc_title_tokens, slsk_title_tokens)

    # Substring check for partial title matches
    if sc_title_norm and slsk_title_norm:
        sc_core = re.sub(r"\b(?:remix|edit|mix|version|extended|radio|club|dirty|clean|explicit)\b", "", sc_title_norm).strip()
        slsk_core = re.sub(r"\b(?:remix|edit|mix|version|extended|radio|club|dirty|clean|explicit)\b", "", slsk_title_norm).strip()
        if sc_core and slsk_core:
            if sc_core in slsk_core or slsk_core in sc_core:
                title_score = max(title_score, 0.7)

    # Version descriptor matching
    sc_versions = extract_versions(sc_title)
    slsk_versions = extract_versions(slsk_title)
    ver_score = version_match_score(sc_versions, slsk_versions)

    # Duration similarity
    duration_score = 0.0
    if sc_duration_s is not None and slsk_duration_s is not None and slsk_duration_s > 0:
        diff = abs(sc_duration_s - slsk_duration_s)
        if diff <= DURATION_TOLERANCE_S:
            duration_score = 1.0 - (diff / DURATION_TOLERANCE_S)
        elif diff <= sc_duration_s * 0.1:
            duration_score = 0.3

    weights = {"artist": 0.30, "title": 0.35, "version": 0.20, "duration": 0.15}
    score = (
        weights["artist"] * artist_score
        + weights["title"] * title_score
        + weights["version"] * ver_score
        + weights["duration"] * duration_score
    )

    # Apply hard version conflict penalty
    if ver_score == 0.0:
        score *= 0.5
        logger.debug(
            "Version conflict penalty applied: %.2f -> %.2f (SC: %s vs SLSK: %s)",
            score / 0.5, score, sc_versions, slsk_versions,
        )

    return score


def filter_and_rank_candidates(
    sc_artist: str,
    sc_title: str,
    sc_duration_s: float | None,
    candidates: list[dict],
) -> list[dict]:
    """Score, filter, and rank Soulseek candidates against a SoundCloud track."""
    scored = []
    for c in candidates:
        slsk_duration = c.get("duration_s")
        score = compute_match_score(
            sc_artist=sc_artist,
            sc_title=sc_title,
            sc_duration_s=sc_duration_s,
            slsk_filename=c["filename"],
            slsk_duration_s=slsk_duration,
        )
        c["match_score"] = score
        if score >= MIN_MATCH_SCORE:
            scored.append(c)
        else:
            logger.debug("Rejected (score %.2f < %.2f): %s", score, MIN_MATCH_SCORE, c["filename"])

    from .config import PREFER_FREE_SLOTS

    def sort_key(c):
        slot_pref = 0 if (PREFER_FREE_SLOTS and c.get("has_free_slots")) else 1
        return (-c["match_score"], c.get("tier", 0), slot_pref, -c.get("avg_speed", 0), -c.get("filesize", 0))

    scored.sort(key=sort_key)

    if scored:
        logger.info(
            "Matched %d/%d candidates for '%s - %s' (top: %.2f: %s)",
            len(scored), len(candidates), sc_artist, sc_title,
            scored[0]["match_score"], scored[0]["filename"],
        )
    else:
        logger.warning(
            "No candidates matched '%s - %s' above threshold %.2f (%d raw results)",
            sc_artist, sc_title, MIN_MATCH_SCORE, len(candidates),
        )

    # Apply hard version conflict penalty
    if ver_score == 0.0:
        score *= 0.5
        logger.debug(
            "Version conflict penalty applied: %.2f -> %.2f (SC: %s vs SLSK: %s)",
            score / 0.5, score, sc_versions, slsk_versions,
        )

    return scored
