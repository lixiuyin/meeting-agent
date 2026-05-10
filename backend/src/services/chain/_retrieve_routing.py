"""Chain-layer routing helpers.

This module owns chunk allocation (``compute_chunk_budget`` and its
``_compute_adaptive_chunks`` / ``_scale_chunks_per_file`` building blocks)
and speaker lookup (``_load_known_speakers``).  The summary-router and
scope enumeration helpers live in :mod:`src.services.rag._routing`;
import them from there directly.
"""

from ...core.config import settings
from ...core.database import get_connection
from ._common import logger

__all__ = [
    "_compute_adaptive_chunks",
    "_load_known_speakers",
    "_scale_chunks_per_file",
    "compute_chunk_budget",
]


# Pre-built SQL templates -- only ``?`` placeholders are dynamic.
_SPEAKERS_BY_MEETINGS_SQL = (
    "SELECT DISTINCT speaker_name FROM speaker_mappings WHERE meeting_id IN ({})"
)
_SPEAKERS_ALL_SQL = "SELECT DISTINCT speaker_name FROM speaker_mappings"


def _load_known_speakers(meeting_ids: list[int] | None) -> list[str]:
    """Load human-readable speaker names from speaker_mappings.

    When meeting_ids is None (no meeting selected), loads speakers from ALL
    meetings so that query analysis can still do high-precision matching.

    IMPORTANT: Only returns ``speaker_name`` values -- raw speaker codes
    like 'A', 'B' are excluded because they cause false-positive matches
    in query analysis (e.g. 'A' matching inside 'Alex').
    """
    try:
        with get_connection() as conn:
            if meeting_ids:
                placeholders = ",".join("?" for _ in meeting_ids)
                sql = _SPEAKERS_BY_MEETINGS_SQL.format(placeholders)
                rows = conn.execute(sql, meeting_ids).fetchall()
            else:
                rows = conn.execute(_SPEAKERS_ALL_SQL).fetchall()
            names: list[str] = []
            seen: set[str] = set()
            for row in rows:
                val = row["speaker_name"]
                if val and val.strip() and len(val.strip()) > 1 and val.strip().lower() not in seen:
                    seen.add(val.strip().lower())
                    names.append(val.strip())
            return names
    except Exception:
        logger.warning("Failed to load known speakers from DB", exc_info=True)
        return []


def _compute_adaptive_chunks(
    scope_file_ids: list[int],
    file_scores: dict[int, float] | None,
    file_metadata: dict[int, dict] | None,
    total_budget: int,
    min_per_file: int,
    max_per_file: int,
) -> int | dict[int, int]:
    """Compute per-file chunk allocation.

    When ``file_scores`` is available, allocates proportionally to relevance.
    Otherwise returns a uniform ``int``.
    """
    if not file_scores or not settings.RAG_FAIR_ADAPTIVE_CHUNKS:
        return max(min_per_file, -(-total_budget // max(len(scope_file_ids), 1)))

    scores = file_scores
    max_score = max(scores.values()) if scores else 1.0
    if max_score <= 0:
        max_score = 1.0

    weights: dict[int, float] = {}
    for fid in scope_file_ids:
        score_norm = scores.get(fid, 0.0) / max_score
        size_factor = 1.0
        if file_metadata and settings.RAG_FAIR_SIZE_FACTOR_ENABLED:
            meta = file_metadata.get(fid, {})
            if meta.get("page_count", 0) > 0:
                size_factor = min(0.5 + meta["page_count"] / 10.0, 3.0)
            elif meta.get("duration_seconds", 0) > 0:
                size_factor = min(0.5 + meta["duration_seconds"] / 300.0, 3.0)
        weights[fid] = max(score_norm, 0.15) * size_factor

    total_weight = sum(weights.values())
    if total_weight <= 0:
        return max(min_per_file, -(-total_budget // max(len(scope_file_ids), 1)))

    allocation: dict[int, int] = {}
    for fid in scope_file_ids:
        share = weights[fid] / total_weight
        budget = max(min_per_file, min(max_per_file, round(share * total_budget)))
        allocation[fid] = budget

    return allocation


def _scale_chunks_per_file(
    chunks_per_file: int | dict[int, int],
    n_queries: int,
) -> int | dict[int, int]:
    """Scale per-file chunk budget across N query variants.

    Floor of 2 chunks per file per variant.
    """
    if n_queries <= 1:
        return chunks_per_file
    if isinstance(chunks_per_file, int):
        return max(chunks_per_file // n_queries, 2)
    return {fid: max(budget // n_queries, 2) for fid, budget in chunks_per_file.items()}


def compute_chunk_budget(
    scope_file_ids: list[int],
    file_scores: dict[int, float] | None,
    file_metadata: dict[int, dict] | None,
    *,
    target_total: int,
    n_variants: int,
    min_per_file: int,
    max_per_file: int,
    uniform_for_single_meeting: bool = False,
) -> int | dict[int, int]:
    """Resolve the per-file (per-variant) chunk budget for fair retrieval.

    Composes the two prior steps:
      1. ``_compute_adaptive_chunks`` distributes ``target_total`` across
         ``scope_file_ids`` weighted by ``file_scores`` and (optionally)
         ``file_metadata`` size signals.
      2. ``_scale_chunks_per_file`` divides each file's budget by the
         number of multi-query variants, with a floor of 2 chunks per
         file per variant.

    When ``uniform_for_single_meeting`` is True, forces uniform allocation
    (each file gets the same budget) regardless of file_scores, ensuring
    balanced coverage across all files in a user-pinned meeting.
    """
    if uniform_for_single_meeting:
        per_file = max(
            target_total // max(len(scope_file_ids), 1),
            min_per_file * 2,
        )
    else:
        per_file = _compute_adaptive_chunks(
            scope_file_ids,
            file_scores,
            file_metadata,
            target_total,
            min_per_file=min_per_file,
            max_per_file=max_per_file,
        )
    return _scale_chunks_per_file(per_file, n_variants)
