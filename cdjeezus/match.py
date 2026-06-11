"""Fuzzy matching between SoundCloud tracks and Soulseek search results.

Most matches are straightforward and resolve to a single download.
Multiple downloads per track only happen when there's genuine version
ambiguity (e.g. Remix vs Original Mix both available with similar scores).
"""

import logging
import re

logger = logging.getLogger(__name__)

MIN_MATCH_SCORE = 0.40
HIGH_CONFIDENCE_SCORE = 0.70
DURATION_TOLERANCE_S = 15

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

    Handles full paths, track numbers, various separators, and version tags.
    """
    import os
    # Handle both Unix (/) and Windows (\\) path separators
    basename = filename.replace("\\", "/").split("/")[-1]
    name = basename.rsplit(".", 1)[0] if "." in basename else basename
    name = re.sub(r"^\d{1,3}[\s.\-]+", "", name)
    parts = re.split(r"\s+-\s+|\s+-|--", name, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    parts = name.rsplit(",", 1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip() and len(parts[1].strip()) > 3:
        return parts[0].strip(), parts[1].strip()
    return "", name.strip()


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[\(\[]?(?:dirty|clean|explicit|radio\s*edit)\s*[\)\]]?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:feat\.?|ft\.?|featuring)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_versions(title: str) -> frozenset[str]:
    versions = set()
    for m in VERSION_PATTERN.finditer(title):
        tag = m.group(1).strip().lower()
        if tag:
            versions.add(tag)
    return frozenset(versions)


def _is_version_conflict(sc_versions: frozenset[str], slsk_versions: frozenset[str]) -> bool:
    """True when the two version sets indicate fundamentally different mixes."""
    if not sc_versions or not slsk_versions:
        return False
    if sc_versions & slsk_versions:
        return False  # some overlap, not a conflict
    original_words = frozenset({"original mix", "original", "o.g.", "og"})
    remix_words = frozenset({"remix", "edit", "mix", "club mix", "club edit", "extended mix", "extended", "dub", "vip", "mashup"})
    return bool(
        (sc_versions & original_words and slsk_versions & remix_words)
        or (sc_versions & remix_words and slsk_versions & original_words)
    )


def version_match_score(sc_versions: frozenset[str], slsk_versions: frozenset[str]) -> float:
    if not sc_versions and not slsk_versions:
        return 1.0
    if not sc_versions or not slsk_versions:
        return 0.7
    overlap = sc_versions & slsk_versions
    if overlap == sc_versions == slsk_versions:
        return 1.0
    if overlap:
        return 0.5
    if _is_version_conflict(sc_versions, slsk_versions):
        return 0.15
    return 0.3


def tokenize(text: str) -> set[str]:
    return set(text.split()) if text else set()


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compute_match_score(
    sc_artist: str,
    sc_title: str,
    sc_duration_s: float | None,
    slsk_filename: str,
    slsk_duration_s: float | None,
) -> float:
    slsk_artist, slsk_title = parse_soulseek_filename(slsk_filename)

    sc_artist_norm = normalize(sc_artist)
    slsk_artist_norm = normalize(slsk_artist)
    artist_score = jaccard_similarity(tokenize(sc_artist_norm), tokenize(slsk_artist_norm))
    if slsk_artist_norm and sc_artist_norm:
        primary_sc = sc_artist_norm.split(",")[0].split("&")[0].strip()
        primary_slsk = slsk_artist_norm.split(",")[0].split("&")[0].strip()
        if primary_sc in slsk_artist_norm or primary_slsk in sc_artist_norm:
            artist_score = max(artist_score, 0.6)

    sc_title_norm = normalize(sc_title)
    slsk_title_norm = normalize(slsk_title)
    title_score = jaccard_similarity(tokenize(sc_title_norm), tokenize(slsk_title_norm))
    if sc_title_norm and slsk_title_norm:
        sc_core = re.sub(r"\b(?:remix|edit|mix|version|extended|radio|club|dirty|clean|explicit)\b", "", sc_title_norm).strip()
        slsk_core = re.sub(r"\b(?:remix|edit|mix|version|extended|radio|club|dirty|clean|explicit)\b", "", slsk_title_norm).strip()
        if sc_core and slsk_core and (sc_core in slsk_core or slsk_core in sc_core):
            title_score = max(title_score, 0.7)

    sc_versions = extract_versions(sc_title)
    slsk_versions = extract_versions(slsk_title)
    ver_score = version_match_score(sc_versions, slsk_versions)

    duration_score = 0.0
    if sc_duration_s is not None and slsk_duration_s is not None and slsk_duration_s > 0:
        diff = abs(sc_duration_s - slsk_duration_s)
        if diff <= DURATION_TOLERANCE_S:
            duration_score = 1.0 - (diff / DURATION_TOLERANCE_S)
        elif diff <= sc_duration_s * 0.1:
            duration_score = 0.3

    weights = {"artist": 0.30, "title": 0.35, "version": 0.20, "duration": 0.15}
    return (
        weights["artist"] * artist_score
        + weights["title"] * title_score
        + weights["version"] * ver_score
        + weights["duration"] * duration_score
    )


def filter_and_rank_candidates(
    sc_artist: str,
    sc_title: str,
    sc_duration_s: float | None,
    candidates: list[dict],
) -> list[dict]:
    """Score, filter, and rank Soulseek candidates.

    Most of the time this returns a single high-confidence candidate.
    Multiple candidates are only included when:
      - The top match is below HIGH_CONFIDENCE_SCORE (ambiguous)
      - AND there are candidates with different version descriptors
        that also pass the threshold (version conflict)
    In those uncertain cases we include one candidate per version group
    so the user can choose the right one and delete the rest.
    """
    from .config import PREFER_FREE_SLOTS

    for c in candidates:
        c["match_score"] = compute_match_score(
            sc_artist=sc_artist,
            sc_title=sc_title,
            sc_duration_s=sc_duration_s,
            slsk_filename=c["filename"],
            slsk_duration_s=c.get("duration_s"),
        )

    scored = [c for c in candidates if c["match_score"] >= MIN_MATCH_SCORE]

    if not scored:
        logger.warning(
            "No candidates matched '%s - %s' above threshold %.2f (%d raw)",
            sc_artist, sc_title, MIN_MATCH_SCORE, len(candidates),
        )
        return []

    # Sort by match score, then quality, then download preference
    def sort_key(c):
        slot_pref = 0 if (PREFER_FREE_SLOTS and c.get("has_free_slots")) else 1
        return (-c["match_score"], c.get("tier", 0), slot_pref, -c.get("avg_speed", 0), -c.get("filesize", 0))

    scored.sort(key=sort_key)

    # If the top match is high-confidence, just return it -- no ambiguity
    if scored[0]["match_score"] >= HIGH_CONFIDENCE_SCORE:
        logger.info(
            "High-confidence match for '%s - %s' (score %.2f): %s",
            sc_artist, sc_title, scored[0]["match_score"], scored[0]["filename"],
        )
        return [scored[0]]

    # Below high confidence: check if there are version-conflicting alternatives
    # that might be the right one instead
    sc_versions = extract_versions(sc_title)

    # Collect one candidate per distinct version group as fallbacks,
    # but only if they're actually plausible (above threshold)
    result: list[dict] = []
    seen_versions: set[frozenset[str]] = set()

    for c in scored:
        versions = extract_versions(c["filename"])
        version_key = versions if versions else frozenset({"_no_version"})

        if version_key in seen_versions:
            continue
        seen_versions.add(version_key)
        result.append(c)

        # Stop once we've covered the SC-requested version + one conflicting version
        # We don't need to download every single variant
        if len(result) >= 2 and len(seen_versions) >= 2:
            break

    logger.info(
        "Ambiguous match for '%s - %s': %d version groups (top %.2f: %s)",
        sc_artist, sc_title, len(result),
        result[0]["match_score"], result[0]["filename"],
    )
    return result
