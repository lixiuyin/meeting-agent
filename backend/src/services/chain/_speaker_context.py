"""Shared helper for building a per-speaker utterance block used by meeting-summary prompts."""

from __future__ import annotations

import json as _json

from ...core import database as _db


def build_speaker_context(files: list[dict]) -> str:
    """Build a per-speaker utterance block from segment data and speaker mappings.

    Reads ``segments_json`` and ``speaker_mappings`` for each file, groups
    utterances by speaker name, and returns a Markdown section the LLM can
    use to populate the Roles section of the summary.  Returns an empty
    string when no speaker data is available.
    """
    speaker_utterances: dict[str, list[str]] = {}
    speaker_name_by_code: dict[str, str] = {}

    for f in files:
        try:
            with _db.get_connection() as conn:
                mappings = _db.list_speaker_mappings(conn, f["id"])
        except Exception:
            mappings = []
        for m in mappings:
            code = m.get("speaker_code", "")
            name = m.get("speaker_name", "")
            if code and name and code not in speaker_name_by_code:
                speaker_name_by_code[code] = name

        segments_json = f.get("segments_json")
        if not segments_json:
            continue
        try:
            segments = (
                _json.loads(segments_json) if isinstance(segments_json, str) else segments_json
            )
        except (_json.JSONDecodeError, TypeError):
            continue
        if not isinstance(segments, list):
            continue

        file_id = f["id"]
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            code = seg.get("speaker", "")
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            name = speaker_name_by_code.get(code, code) if code else "Unknown"
            label = f"{name} [file:{file_id}]"
            speaker_utterances.setdefault(label, []).append(text)

    if not speaker_utterances:
        return ""

    max_per_speaker = 10
    lines: list[str] = ["## Speaker utterances (sampled)", ""]
    for speaker, utterances in sorted(speaker_utterances.items()):
        sample = utterances[:max_per_speaker]
        combined = " | ".join(sample)
        if len(utterances) > max_per_speaker:
            combined += f"  … (+{len(utterances) - max_per_speaker} more)"
        lines.append(f"- **{speaker}**: {combined}")
    return "\n".join(lines)
