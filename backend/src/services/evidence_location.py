"""Resolve references against canonical per-file text, never generated answers."""

import hashlib
import json
import re

from ..models.schemas.evidence import EvidenceLocationRequest


def resolve_evidence_location(
    source: str, timeline: dict, request: EvidenceLocationRequest
) -> dict:
    start = request.window_start or 0
    end = request.window_end if request.window_end is not None else len(source)
    if end > len(source):
        return {"status": "not_found", "reason": "invalid_window"}
    kind = timeline.get("kind")
    parts = timeline.get("pages", []) if kind == "pages" else timeline.get("segments", [])
    blocks = [block for part in parts for block in part.get("blocks", [])]
    if request.block_id:
        selected = [b for b in blocks if b.get("block_id") == request.block_id]
        if len(selected) != 1:
            return {"status": "not_found", "reason": "missing_block"}
        block = selected[0]
        if source[block["window_start"] : block["window_end"]] != block.get("text"):
            return {"status": "not_found", "reason": "block_text_mismatch"}
        start, end = max(start, block["window_start"]), min(end, block["window_end"])
    spans: list[tuple[int, int, dict]] = []
    cursor = 0
    for part in parts:
        text = part.get("text") or ""
        if not text.strip():
            continue
        position = source.find(text, cursor)
        if position < 0:
            return {"status": "not_found", "reason": "parser_text_mismatch"}
        cursor = position + len(text)
        spans.append((position, cursor, part))
    if request.page:
        page_span = next((span for span in spans if span[2].get("page_num") == request.page), None)
        if not page_span:
            return {"status": "not_found", "reason": "missing_page"}
        start, end = max(start, page_span[0]), min(end, page_span[1])
    if end <= start:
        return {"status": "not_found", "reason": "empty_window"}
    excerpt = (request.excerpt or "").strip()
    if excerpt:
        pattern = r"\s+".join(re.escape(word) for word in excerpt.split())
        matches = list(re.finditer(pattern, source[start:end]))
        if len(matches) != 1:
            return {
                "status": "ambiguous" if matches else "not_found",
                "reason": "quote_not_unique" if matches else "quote_missing",
            }
        start, end = start + matches[0].start(), start + matches[0].end()
    elif request.window_start is None and request.page is None and not request.block_id:
        return {"status": "not_found", "reason": "missing_locator"}
    result = {
        "status": "exact",
        "window_start": start,
        "window_end": end,
        "excerpt": source[start:end] if excerpt else None,
    }
    matching_blocks = [b for b in blocks if b["window_start"] <= start and b["window_end"] >= end]
    if len(matching_blocks) == 1:
        result["block_id"] = matching_blocks[0]["block_id"]
    if kind in ("text", "captions"):
        return result
    hits = [part for left, right, part in spans if left < end and right > start]
    if not hits:
        return {"status": "not_found", "reason": "unmapped_window"}
    if kind == "pages":
        result["page"] = hits[0]["page_num"]
        if not excerpt or len(hits) > 1:
            result.update(status="page_only", reason="window_not_single_passage")
    elif kind == "segments":
        result.update(timestamp_start=hits[0]["start"], timestamp_end=hits[-1]["end"])
    return result


def evidence_identity(
    meeting_id: int, file_id: int, source_revision: str, parser_revision: str, location: dict
) -> str:
    value = [
        meeting_id,
        file_id,
        source_revision,
        parser_revision,
        location.get("window_start"),
        location.get("window_end"),
        location.get("page"),
    ]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()
