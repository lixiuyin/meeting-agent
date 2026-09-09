"""Deterministic contextual retrieval text with lossless display restoration."""

from __future__ import annotations

from typing import Any

from ...core.material_role import infer_material_role as infer_material_role

_PREFIX_LEN_KEY = "retrieval_context_prefix_len"


def contextualize_content(content: str, metadata: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Prefix a chunk with stable local context used only by retrieval indexes."""
    from ._meeting_structure import structure_metadata

    if metadata.get("material_role") == "transcript":
        metadata = {**metadata, **structure_metadata(content)}
    fields: list[str] = []
    for label, names in (
        ("meeting", ("meeting_title", "title")),
        ("file", ("file_name",)),
        ("section", ("heading_path", "section_title", "slide_title")),
        ("speaker", ("speaker",)),
        ("date", ("meeting_date", "date")),
        ("role", ("material_role",)),
        ("approval", ("approval_status",)),
        ("context", ("context_hint",)),
    ):
        value = next((metadata.get(name) for name in names if metadata.get(name)), None)
        if isinstance(value, (list, tuple)):
            value = " > ".join(str(item) for item in value if item)
        if label == "context" and value:
            value = " ".join(str(value).split())[:400]
        if value not in (None, "", "unknown"):
            fields.append(f"{label}={value}")
    start = metadata.get("timestamp_start")
    end = metadata.get("timestamp_end")
    if isinstance(start, (int, float)):
        time_label = (
            f"time={start:.1f}-{float(end):.1f}s"
            if isinstance(end, (int, float))
            else f"time={start:.1f}s"
        )
        fields.append(time_label)
    if not fields:
        return content, metadata
    prefix = "[Retrieval context: " + "; ".join(fields) + "]\n"
    enriched = {**metadata, _PREFIX_LEN_KEY: len(prefix)}
    return prefix + content, enriched


def restore_display_content(content: str, metadata: dict[str, Any] | None) -> str:
    """Remove the deterministic retrieval-only prefix from a stored chunk."""
    try:
        prefix_len = int((metadata or {}).get(_PREFIX_LEN_KEY) or 0)
    except (TypeError, ValueError):
        return content
    if prefix_len <= 0 or prefix_len > len(content):
        return content
    return content[prefix_len:]
