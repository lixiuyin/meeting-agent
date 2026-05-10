"""Post-retrieval filtering, bias, and scoring functions.

All public functions return **new** collections and never mutate their inputs,
following the project's immutability convention.
"""

import re

from ...core.database import get_connection, get_file_ids_for_speakers
from ..rag._indexer import split_speakers
from ..rag._query_analysis import QueryAnalysis, TemporalHint
from ._common import logger
from ._retrieve_utils import (
    _CONTENT_TYPES_BIAS_FIGURE,
    _CONTENT_TYPES_BIAS_TABLE,
    _FIGURE_HINTS,
    _TABLE_HINTS,
    _content_bias_boost,
)

_SPEAKER_TEMPORAL_BOOST = 0.10


def _apply_content_type_bias(query: str, docs: list[dict]) -> list[dict]:
    """Adjust reranker scores when query intent hints at specific content types.

    Returns a **new** sorted list; does not mutate inputs.
    """
    lowered = query.lower()
    boost_table = any(h in lowered for h in _TABLE_HINTS)
    boost_figure = any(h in lowered for h in _FIGURE_HINTS)
    if not boost_table and not boost_figure:
        return docs
    result: list[dict] = []
    for doc in docs:
        meta = doc.get("metadata") or {}
        content_type = str(meta.get("content_type", "")).lower()
        score = float(doc.get("score", 0.0))
        boost = 0.0
        if boost_table and content_type in _CONTENT_TYPES_BIAS_TABLE:
            boost += _content_bias_boost(doc)
        if boost_figure and content_type in _CONTENT_TYPES_BIAS_FIGURE:
            boost += _content_bias_boost(doc)
        new_doc = dict(doc)
        new_doc["score"] = score + boost
        result.append(new_doc)
    return sorted(result, key=lambda d: float(d.get("score", 0.0)), reverse=True)


def _apply_speaker_filter(
    docs: list[dict], qa: QueryAnalysis, meeting_ids: list[int] | None
) -> list[dict]:
    """Hard-filter docs to only include files where the target speaker exists.

    Returns a **new** list; does not mutate inputs.

    Strategy (layered, high→low precision):
    1. Query ``speaker_mappings`` DB table to get file_ids that have the
       target speaker — this is authoritative.
    2. For chunks without file_id or from files not in speaker_mappings,
       check chunk metadata (``speaker``, ``speakers_in_chunk``) and
       chunk content text for the speaker name — fallback for old data.
    3. If hard filter would remove ALL results, keep everything (graceful
       degradation) but still apply temporal boost and re-sort.

    After filtering, applies temporal boost for time-position queries.
    """
    if not qa.speaker_names:
        return docs

    target_names_lower = {n.lower() for n in qa.speaker_names}

    # Layer 1: DB lookup for file_ids that have the target speaker
    valid_file_ids: set[int] = set()
    try:
        with get_connection() as conn:
            valid_file_ids = get_file_ids_for_speakers(
                conn, qa.speaker_names, meeting_ids=meeting_ids
            )
    except Exception:
        logger.warning(
            "speaker_mappings lookup failed, falling back to content check", exc_info=True
        )

    if valid_file_ids:
        logger.info(
            "Speaker filter: files containing %s → file_ids=%s",
            qa.speaker_names,
            valid_file_ids,
        )

    _name_patterns = [
        re.compile(r"(?:^|(?<=[^a-zA-Z一-鿿]))" + re.escape(n) + r"(?=$|[^a-zA-Z一-鿿])")
        for n in target_names_lower
    ]

    kept: list[dict] = []
    for doc in docs:
        meta = doc.get("metadata") or {}
        file_id = meta.get("file_id")

        # Layer 1: file in speaker_mappings
        if isinstance(file_id, int) and file_id in valid_file_ids:
            kept.append(doc)
            continue

        # Layer 2: chunk metadata has the speaker
        speakers_raw = str(meta.get("speakers_in_chunk", ""))
        speaker_primary = str(meta.get("speaker", ""))
        chunk_speakers = {s.lower() for s in split_speakers(speakers_raw)}
        chunk_speakers.add(speaker_primary.lower())
        if chunk_speakers & target_names_lower:
            kept.append(doc)
            continue

        # Layer 3: speaker name appears as a standalone word in chunk content
        content_lower = str(doc.get("content", "")).lower()
        if any(p.search(content_lower) for p in _name_patterns):
            kept.append(doc)
            continue

    # Graceful degradation: if hard filter removes everything, keep all
    if not kept:
        logger.warning(
            "Speaker hard-filter removed all %d results for speakers=%s; keeping all",
            len(docs),
            qa.speaker_names,
        )
        kept = list(docs)

    # Apply temporal filter on surviving docs
    if qa.temporal_hint:
        kept = _apply_temporal_filter(kept, qa.temporal_hint)

    removed = len(docs) - len(kept)
    if removed:
        logger.info(
            "Speaker hard-filter: %d → %d docs (removed %d from non-speaker files)",
            len(docs),
            len(kept),
            removed,
        )
    return kept


def _apply_temporal_filter(docs: list[dict], hint: TemporalHint) -> list[dict]:
    """Filter docs by time position, keeping chunks that overlap the target range.

    Returns a **new** list; does not mutate inputs.  Boosted docs are
    shallow-copied so the original ``score`` values remain untouched.

    Boundary policy: if a chunk partially overlaps the target time window,
    keep the entire chunk (no mid-sentence cuts).

    Supports two modes:
    - Absolute seconds: uses ``timestamp_start`` / ``timestamp_end`` metadata
      combined with ``meeting_duration`` for precise filtering.
    - Ratio-based: uses ``time_position_ratio`` when absolute times aren't
      available or the hint is ratio-only.

    When the filter would remove ALL docs, returns the originals unchanged
    (graceful degradation).
    """
    if not docs:
        return docs

    temporal_kept: list[dict] = []
    temporal_boosted: list[dict] = []

    for doc in docs:
        meta = doc.get("metadata") or {}
        ts_start = meta.get("timestamp_start")
        ts_end = meta.get("timestamp_end")
        duration = float(meta.get("meeting_duration", 0.0))
        ratio = float(meta.get("time_position_ratio", -1.0))

        in_range = False
        is_boosted = False

        if hint.absolute_seconds is not None and duration > 0:
            abs_lo, abs_hi = hint.absolute_seconds
            range_start = abs_lo if abs_lo >= 0 else max(0.0, duration + abs_lo)
            range_end = abs_hi if abs_hi > 0 else duration + abs_hi
            range_end = min(range_end, duration)

            if ts_start is not None and ts_end is not None:
                chunk_start = float(ts_start)
                chunk_end = float(ts_end)
                if chunk_start < range_end and chunk_end > range_start:
                    in_range = True
                    overlap = min(chunk_end, range_end) - max(chunk_start, range_start)
                    chunk_len = max(chunk_end - chunk_start, 0.001)
                    if overlap / chunk_len > 0.5:
                        is_boosted = True
            elif ts_start is not None:
                chunk_start = float(ts_start)
                if range_start <= chunk_start <= range_end:
                    in_range = True
                    is_boosted = True
        elif 0.0 <= ratio <= 1.0 and hint.absolute_seconds is None:
            chunk_lo = max(0.0, ratio - 0.05)
            chunk_hi = min(1.0, ratio + 0.05)
            if chunk_lo < hint.ratio_max and chunk_hi > hint.ratio_min:
                in_range = True
                if hint.ratio_min <= ratio <= hint.ratio_max:
                    is_boosted = True

        if in_range:
            if is_boosted:
                new_doc = dict(doc)
                new_doc["score"] = float(doc.get("score", 0.0)) + _SPEAKER_TEMPORAL_BOOST
                temporal_boosted.append(new_doc)
            else:
                temporal_kept.append(doc)

    result = temporal_boosted + temporal_kept
    if not result:
        logger.warning(
            "Temporal filter removed all %d docs for hint=%s; keeping all",
            len(docs),
            hint,
        )
        return docs

    result.sort(key=lambda d: float(d.get("score", 0.0)), reverse=True)
    logger.info(
        "Temporal filter: %d → %d docs (hint=%s)",
        len(docs),
        len(result),
        hint,
    )
    return result
