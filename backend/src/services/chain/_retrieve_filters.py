"""Post-retrieval filtering, bias, and scoring functions.

All public functions return **new** collections and never mutate their inputs,
following the project's immutability convention.
"""

import datetime
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
_DECISION_HINTS = ("decision", "decided", "approved", "决策", "决定", "批准")
_ACTION_HINTS = (
    "action item",
    "todo",
    "follow-up",
    "owner",
    "deadline",
    "行动项",
    "待办",
    "负责人",
    "截止",
)
_SUMMARY_HINTS = ("summary", "summarize", "overview", "recap", "总结", "概述", "回顾")
_REJECTED_EVIDENCE_HINTS = ("rejected", "withdrawn", "retracted", "撤回", "拒绝", "驳回")


def _as_utc(value: object) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def apply_meeting_evidence_policy(
    docs: list[dict],
    *,
    query: str,
    user_id: str,
    known_at: datetime.datetime | None = None,
) -> list[dict]:
    """Apply current review policy and system-time visibility to retrieved chunks."""
    if not docs:
        return []
    file_ids = {
        int(file_id)
        for doc in docs
        if isinstance((file_id := (doc.get("metadata") or {}).get("file_id")), int)
    }
    records: dict[int, dict] = {}
    if file_ids:
        try:
            with get_connection() as conn:
                ordered_ids = sorted(file_ids)
                for start in range(0, len(ordered_ids), 500):
                    batch = ordered_ids[start : start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    rows = conn.execute(
                        "SELECT mf.* FROM meeting_files mf JOIN meetings m ON m.id=mf.meeting_id "
                        f"WHERE mf.id IN ({placeholders}) AND m.user_id=?",
                        (*batch, user_id),
                    ).fetchall()
                    records.update({int(row["id"]): dict(row) for row in rows})
        except Exception:
            logger.warning("Meeting evidence policy lookup failed closed", exc_info=True)
            return []

    include_rejected = any(term in query.casefold() for term in _REJECTED_EVIDENCE_HINTS)
    cutoff = known_at.astimezone(datetime.UTC) if known_at and known_at.tzinfo else known_at
    kept: list[dict] = []
    for doc in docs:
        metadata = dict(doc.get("metadata") or {})
        file_id = metadata.get("file_id")
        record = records.get(file_id) if isinstance(file_id, int) else None
        if isinstance(file_id, int) and record is None:
            continue
        indexed_revision = metadata.get("file_source_revision")
        if record is not None and indexed_revision is not None:
            try:
                if int(indexed_revision) != int(record.get("source_revision") or 1):
                    continue
            except (TypeError, ValueError):
                continue
        approval = str(
            (record or {}).get("approval_status") or metadata.get("approval_status") or "unreviewed"
        ).lower()
        if approval == "rejected" and not include_rejected:
            continue
        if cutoff is not None:
            recorded_at = _as_utc(
                (record or {}).get("content_recorded_at")
                or (record or {}).get("created_at")
                or metadata.get("document_recorded_at")
            )
            if recorded_at is None or recorded_at > cutoff:
                continue
        if record:
            metadata.update(
                {
                    "material_role": record.get("material_role")
                    or metadata.get("material_role")
                    or "attachment",
                    "approval_status": approval,
                    "approval_reason": record.get("approval_reason"),
                    "file_source_revision": int(record.get("source_revision") or 1),
                    "document_recorded_at": record.get("content_recorded_at")
                    or record.get("created_at"),
                }
            )
        kept.append({**doc, "metadata": metadata})
    return kept


def _material_role_boost(query: str, material_role: str, approval_status: str = "") -> float:
    """Return a small meeting-domain prior without overpowering relevance."""
    lowered = query.lower()
    if any(hint in lowered for hint in _DECISION_HINTS):
        base = {"decision_log": 0.08, "minutes": 0.05, "transcript": 0.02}.get(material_role, 0.0)
    elif any(hint in lowered for hint in _ACTION_HINTS):
        base = {"decision_log": 0.06, "minutes": 0.05, "transcript": 0.02}.get(material_role, 0.0)
    elif any(hint in lowered for hint in _SUMMARY_HINTS):
        base = {"minutes": 0.06, "transcript": 0.04, "agenda": 0.01}.get(material_role, 0.0)
    else:
        base = 0.0
    multiplier = {"draft": 0.35, "reviewed": 0.75, "approved": 1.0}.get(
        approval_status,
        1.0,
    )
    return base * multiplier


def _apply_content_type_bias(query: str, docs: list[dict]) -> list[dict]:
    """Adjust reranker scores when query intent hints at specific content types.

    Returns a **new** sorted list; does not mutate inputs.
    """
    lowered = query.lower()
    boost_table = any(h in lowered for h in _TABLE_HINTS)
    boost_figure = any(h in lowered for h in _FIGURE_HINTS)
    include_rejected = any(term in lowered for term in _REJECTED_EVIDENCE_HINTS)
    result: list[dict] = []
    for doc in docs:
        meta = doc.get("metadata") or {}
        approval_status = str(meta.get("approval_status", "")).lower()
        # Rejected material remains searchable for audit questions, but must
        # never silently support a current factual answer.
        if approval_status == "rejected" and not include_rejected:
            continue
        content_type = str(meta.get("content_type", "")).lower()
        score = float(doc.get("score", 0.0))
        boost = {"draft": -0.03, "unreviewed": -0.01}.get(approval_status, 0.0)
        if boost_table and content_type in _CONTENT_TYPES_BIAS_TABLE:
            boost += _content_bias_boost(doc)
        if boost_figure and content_type in _CONTENT_TYPES_BIAS_FIGURE:
            boost += _content_bias_boost(doc)
        boost += _material_role_boost(
            lowered,
            str(meta.get("material_role", "")).lower(),
            approval_status,
        )
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
    1. Match chunk-level ``speaker`` / ``speakers_in_chunk`` metadata.
    2. Check the chunk text for the speaker name as a legacy fallback.

    A file-level speaker mapping only proves that a person appears somewhere
    in the file. It cannot attribute every chunk in that file to that speaker,
    so it is used for diagnostics but never as a chunk match. Explicit speaker
    constraints fail closed when no chunk is attributable.

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

        # Layer 1: chunk metadata has the speaker. When explicit metadata is
        # present, a mismatch is authoritative and content fallback must not
        # override it merely because another person is mentioned in the text.
        speakers_raw = str(meta.get("speakers_in_chunk", ""))
        speaker_primary = str(meta.get("speaker", ""))
        chunk_speakers = {s.lower() for s in split_speakers(speakers_raw)}
        if speaker_primary.strip():
            chunk_speakers.add(speaker_primary.lower())
        chunk_speakers.discard("")
        if chunk_speakers:
            if chunk_speakers & target_names_lower:
                kept.append(doc)
            continue

        # Layer 2: speaker name appears as a standalone word in chunk content.
        content_lower = str(doc.get("content", "")).lower()
        if any(p.search(content_lower) for p in _name_patterns):
            kept.append(doc)
            continue

    if not kept:
        logger.warning(
            "Speaker hard-filter removed all %d results for speakers=%s",
            len(docs),
            qa.speaker_names,
        )

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

    Explicit temporal constraints fail closed: if no chunk overlaps the
    requested range, return no evidence instead of silently widening scope.
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
            "Temporal filter removed all %d docs for explicit hint=%s",
            len(docs),
            hint,
        )
        return []

    result.sort(key=lambda d: float(d.get("score", 0.0)), reverse=True)
    logger.info(
        "Temporal filter: %d → %d docs (hint=%s)",
        len(docs),
        len(result),
        hint,
    )
    return result
