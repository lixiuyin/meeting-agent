"""Hierarchical (funnel) RAG: meetings -> files -> chunks.

Single-pass score aggregation over a wide chunk fetch.  Meeting and file
relevance are derived by coarsening chunk-level scores, so no extra
embedding / Chroma round-trips are needed.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict

from ...core.config import settings
from ...core.database import get_connection

logger = logging.getLogger(__name__)

# Reference chunk-count for the log-normalised fairness factor.  A file with
# this many chunks gets a normalised count component of ~1.0; smaller files
# get less, larger files only marginally more (log compression).
_CHUNK_COUNT_REFERENCE = 8.0


def normalize_scores(docs: list[dict], *, lower_is_better: bool) -> list[dict]:
    """Return shallow copies with scores normalised to [0, 1], higher-better.

    Explicit per-document provenance wins over the legacy direction argument,
    which prevents mixed vector/RRF/multimodal pools from being normalized in
    the wrong direction.
    """
    from ._vector import normalize_document_scores

    return normalize_document_scores(docs, legacy_lower_is_better=lower_is_better)


def aggregate_by_meeting(
    docs: list[dict],
    *,
    top_n: int,
    method: str = "top_k_mean",
    agg_top_k: int = 3,
    title_prior: dict[int, float] | None = None,
) -> list[int]:
    """Aggregate chunk scores by meeting_id and return top-N meeting ids.

    ``title_prior`` is an optional ``{meeting_id: boost}`` mapping (additive).
    """
    groups: dict[int, list[float]] = defaultdict(list)
    for doc in docs:
        mid = (doc.get("metadata") or {}).get("meeting_id")
        if isinstance(mid, int):
            groups[mid].append(float(doc.get("score", 0.0)))

    scored: list[tuple[float, int]] = []
    for mid, scores in groups.items():
        agg = _aggregate_scores(scores, method=method, agg_top_k=agg_top_k)
        if title_prior and mid in title_prior:
            agg += title_prior[mid]
        scored.append((agg, mid))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [mid for _, mid in scored[:top_n]]


def aggregate_by_file_scored(
    docs: list[dict],
    *,
    meeting_ids: list[int] | None = None,
    top_n: int,
    method: str = "top_k_mean",
    agg_top_k: int = 3,
    prior_weights: dict[int, float] | None = None,
    chunk_count_alpha: float | None = None,
    prior_mode: str | None = None,
) -> list[tuple[int, float]]:
    """Aggregate chunk scores by file_id, returning (file_id, agg_score) pairs.

    ``prior_weights`` is an optional ``{file_id: boost}`` mapping applied after
    aggregation.  ``prior_mode`` controls how: ``additive`` (default) adds the
    prior to the score; ``multiplicative`` multiplies by ``(1 + prior)`` for a
    stronger effect on title matches.

    ``chunk_count_alpha`` (0.0-1.0) blends the per-file aggregated score with a
    log-normalised chunk count, letting dense files compete fairly against
    sparse files that happen to have a single very high score.
    """
    configured_alpha = (
        settings.RAG_FUNNEL_AGGREGATION_ALPHA if chunk_count_alpha is None else chunk_count_alpha
    )
    chunk_count_alpha = max(0.0, min(1.0, configured_alpha or 0.0))
    if prior_mode is None:
        prior_mode = settings.RAG_FUNNEL_FILE_PRIOR_MODE

    meeting_set: set[int] | None = set(meeting_ids) if meeting_ids else None
    groups: dict[int, list[float]] = defaultdict(list)
    for doc in docs:
        meta = doc.get("metadata") or {}
        mid = meta.get("meeting_id")
        fid = meta.get("file_id")
        if not isinstance(fid, int):
            continue
        if meeting_set is not None and mid not in meeting_set:
            continue
        groups[fid].append(float(doc.get("score", 0.0)))

    scored: list[tuple[float, int]] = []
    for fid, scores in groups.items():
        quality = _aggregate_scores(scores, method=method, agg_top_k=agg_top_k)
        if chunk_count_alpha < 1.0:
            count_component = _log_normalised_count(len(scores))
            agg = chunk_count_alpha * quality + (1.0 - chunk_count_alpha) * count_component
        else:
            agg = quality
        if prior_weights and fid in prior_weights:
            prior = prior_weights[fid]
            if prior_mode == "multiplicative":
                agg *= 1.0 + prior
            else:
                agg += prior
        scored.append((agg, fid))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [(fid, score) for score, fid in scored[:top_n]]


def _log_normalised_count(n: int) -> float:
    """Map a chunk count to [0, 1+]: log(1+n) / log(1 + reference).

    A file with ``_CHUNK_COUNT_REFERENCE`` chunks gets ~1.0; smaller files
    get less; very large files only slightly more (diminishing returns).
    """
    if n <= 0:
        return 0.0
    ref = math.log1p(_CHUNK_COUNT_REFERENCE)
    return math.log1p(n) / ref if ref > 0 else 0.0


def aggregate_by_file(
    docs: list[dict],
    *,
    meeting_ids: list[int] | None = None,
    top_n: int,
    method: str = "top_k_mean",
    agg_top_k: int = 3,
) -> list[int]:
    """Aggregate chunk scores by file_id (optionally within selected meetings)."""
    scored = aggregate_by_file_scored(
        docs,
        meeting_ids=meeting_ids,
        top_n=top_n,
        method=method,
        agg_top_k=agg_top_k,
    )
    return [fid for fid, _ in scored]


def restrict_pool(
    docs: list[dict],
    *,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> list[dict]:
    """Filter *docs* to only those matching the given meeting/file ids.

    Creates a new list (immutable pattern).
    """
    meeting_set: set[int] | None = set(meeting_ids) if meeting_ids else None
    file_set: set[int] | None = set(file_ids) if file_ids else None

    out: list[dict] = []
    for doc in docs:
        meta = doc.get("metadata") or {}
        mid = meta.get("meeting_id")
        fid = meta.get("file_id")
        if meeting_set is not None and mid not in meeting_set:
            continue
        if file_set is not None and fid not in file_set:
            continue
        out.append(doc)
    return out


def fetch_title_priors(query: str, candidate_meeting_ids: list[int]) -> dict[int, float]:
    """SQL-based title prior: boost meetings whose title/description match query tokens.

    Returns ``{meeting_id: total_boost}`` capped at ``RAG_FUNNEL_TITLE_PRIOR_CAP``.
    """
    if not candidate_meeting_ids or not query.strip():
        return {}

    tokens = _extract_prior_tokens(query)
    if not tokens:
        return {}

    weight = settings.RAG_FUNNEL_TITLE_PRIOR_WEIGHT
    cap = settings.RAG_FUNNEL_TITLE_PRIOR_CAP
    priors: dict[int, float] = {}

    try:
        with get_connection() as conn:
            placeholders = ",".join("?" for _ in candidate_meeting_ids)
            rows = conn.execute(
                f"SELECT id, title, description FROM meetings WHERE id IN ({placeholders})",
                candidate_meeting_ids,
            ).fetchall()
            for row in rows:
                mid = row["id"]
                boost = 0.0
                title_lower = (row["title"] or "").lower()
                desc_lower = (row["description"] or "").lower()
                for tok in tokens:
                    if tok in title_lower:
                        boost += weight
                    if tok in desc_lower:
                        boost += weight * 0.5
                if boost > 0:
                    priors[mid] = min(boost, cap)
    except Exception:
        logger.warning("Title prior query failed", exc_info=True)

    return priors


def _aggregate_scores(
    scores: list[float],
    *,
    method: str,
    agg_top_k: int,
) -> float:
    """Compute a single aggregate score from a list of chunk scores."""
    if not scores:
        return 0.0
    if method == "max":
        return max(scores)
    if method == "count":
        threshold = 0.1
        return float(sum(1 for s in scores if s >= threshold))
    # Default: top_k_mean
    sorted_scores = sorted(scores, reverse=True)
    top = sorted_scores[:agg_top_k]
    return sum(top) / len(top)


_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9一-鿿]+")
_TOKEN_MIN_LEN = 4  # Only tokens longer than 3 chars get boosted


def _extract_prior_tokens(query: str) -> list[str]:
    """Extract meaningful tokens from a query for title matching."""
    raw = _TOKEN_PATTERN.findall(query.lower())
    return [t for t in raw if len(t) >= _TOKEN_MIN_LEN]


def fetch_file_title_priors(
    query: str,
    file_ids: list[int],
) -> dict[int, float]:
    """SQL-based title prior for files: boost files whose meeting title matches query tokens.

    Returns ``{file_id: total_boost}`` capped at ``RAG_FUNNEL_FILE_PRIOR_CAP``.

    Adds a ``RAG_FUNNEL_FILE_PRIOR_FULL_MATCH_BONUS`` if the query fully
    contains the title, or vice versa — a strong "user is asking about this
    document" signal.  The sum (per-token boost + full-match bonus) is still
    capped at ``cap`` so this prior cannot dominate a high funnel score.
    """
    if not file_ids or not query.strip():
        return {}

    tokens = _extract_prior_tokens(query)
    if not tokens:
        return {}

    weight = settings.RAG_FUNNEL_FILE_PRIOR_WEIGHT
    cap = settings.RAG_FUNNEL_FILE_PRIOR_CAP
    full_match_bonus = settings.RAG_FUNNEL_FILE_PRIOR_FULL_MATCH_BONUS
    query_lower = query.strip().lower()
    priors: dict[int, float] = {}

    try:
        with get_connection() as conn:
            placeholders = ",".join("?" for _ in file_ids)
            rows = conn.execute(
                f"SELECT id, meeting_id FROM meeting_files WHERE id IN ({placeholders})",
                file_ids,
            ).fetchall()
            meeting_ids = list({r["meeting_id"] for r in rows})
            if not meeting_ids:
                return {}

            file_to_meeting = {r["id"]: r["meeting_id"] for r in rows}

            m_placeholders = ",".join("?" for _ in meeting_ids)
            meetings = conn.execute(
                f"SELECT id, title, description FROM meetings WHERE id IN ({m_placeholders})",
                meeting_ids,
            ).fetchall()

            meeting_boosts: dict[int, float] = {}
            for row in meetings:
                boost = 0.0
                title_lower = (row["title"] or "").strip().lower()
                desc_lower = (row["description"] or "").lower()
                for tok in tokens:
                    if tok in title_lower:
                        boost += weight
                    if tok in desc_lower:
                        boost += weight * 0.5
                if (
                    title_lower
                    and full_match_bonus > 0
                    and (title_lower in query_lower or query_lower in title_lower)
                ):
                    boost += full_match_bonus
                if boost > 0:
                    meeting_boosts[row["id"]] = min(boost, cap)

            for fid, mid in file_to_meeting.items():
                if mid in meeting_boosts:
                    priors[fid] = meeting_boosts[mid]
    except Exception:
        logger.warning("File title prior query failed", exc_info=True)

    return priors
