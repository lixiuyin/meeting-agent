"""Deterministic meeting units; labels describe text, never certify a decision."""

import hashlib
import re
from dataclasses import dataclass

_BOUNDARY = re.compile(
    r"(?m)^(?:#{1,4}\s+|(?:议题|议程|Agenda|Topic)\s*[:\uFF1A]|"
    r"(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?[^\n:\uFF1A]{1,40}[:\uFF1A]\s*)"
)
_TOPIC = re.compile(r"^(?:#{1,4}\s+|(?:议题|议程|Agenda|Topic)\s*[:\uFF1A])", re.I)


@dataclass(frozen=True)
class MeetingUnit:
    start: int
    end: int
    unit_id: str
    topic_id: str


def meeting_units(text: str) -> list[MeetingUnit]:
    starts = sorted({0, *(m.start() for m in _BOUNDARY.finditer(text))})
    units = []
    topic = "unassigned"
    for start, end in zip(starts, [*starts[1:], len(text)], strict=True):
        content = text[start:end]
        if not content.strip():
            continue
        digest = hashlib.sha256(content.encode()).hexdigest()[:16]
        if _TOPIC.match(content):
            topic = digest
        units.append(MeetingUnit(start, end, f"turn:{start}:{digest}", topic))
    return units


def structure_metadata(text: str) -> dict:
    units = meeting_units(text)
    return {
        "meeting_structure_version": "turn-topic-v1",
        "turn_ids": "|".join(unit.unit_id for unit in units),
        "topic_ids": "|".join(dict.fromkeys(unit.topic_id for unit in units)),
    }


def contextual_window(text: str, start: int, end: int, *, max_chars: int = 6000):
    """Extend a unique hit to adjacent turns in the same explicit topic."""
    units = meeting_units(text)
    hits = [i for i, unit in enumerate(units) if unit.start < end and unit.end > start]
    if not hits or len(units) < 2 or end - start > max_chars:
        return start, end
    first, last = hits[0], hits[-1]
    left, right = first, last
    if first and units[first - 1].topic_id == units[first].topic_id:
        left -= 1
    if last + 1 < len(units) and units[last + 1].topic_id == units[last].topic_id:
        right += 1
    lo, hi = min(start, units[left].start), max(end, units[right].end)
    return (lo, hi) if hi - lo <= max_chars else (start, end)


def expand_meeting_evidence(docs: list[dict], *, user_id: str, query: str) -> list[dict]:
    """Bounded source expansion, after ownership/version admission, before rerank."""
    if not re.search(
        r"为什么|为何|原因|改变|修改|决定|决策|\b(?:why|changed?|decision|rationale)\b", query, re.I
    ):
        return docs
    from ...core import database as db

    files = {}
    result = []
    with db.get_connection() as conn:
        for index, doc in enumerate(docs):
            metadata = doc.get("metadata") or {}
            file_id = metadata.get("file_id")
            content = doc.get("content") or ""
            if index >= 6 or not isinstance(file_id, int) or not content.strip():
                result.append(doc)
                continue
            if file_id not in files:
                files[file_id] = db.get_meeting_file(conn, file_id, user_id=user_id)
            file = files[file_id]
            text = (file or {}).get("transcript") or ""
            if not file or file.get("material_role") != "transcript" or text.count(content) != 1:
                result.append(doc)
                continue
            from ...core.source_revision_fence import meeting_file_source_token

            indexed_revision = metadata.get("file_source_revision")
            if indexed_revision is None or str(indexed_revision) != str(
                file.get("source_revision") or 1
            ):
                result.append(doc)
                continue
            start = text.index(content)
            lo, hi = contextual_window(text, start, start + len(content))
            result.append(
                {
                    **doc,
                    "content": text[lo:hi],
                    "metadata": {
                        **metadata,
                        "context_expanded": lo != start or hi != start + len(content),
                        "evidence_start_offset": lo,
                        "evidence_end_offset": hi,
                        "expansion_source_token": meeting_file_source_token(file),
                        **structure_metadata(text[lo:hi]),
                    },
                }
            )
    return result
