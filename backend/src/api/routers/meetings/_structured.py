"""Shared structured-JSON helpers for meeting file exports and timelines."""

import json
from typing import Any


def parse_structured_json(file_record: dict) -> Any | None:
    """Parse ``structured_json`` from a meeting-file record.

    Returns the decoded object, or ``None`` on missing / invalid JSON.
    """
    raw = file_record.get("structured_json")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def normalize_page_image_assets(raw: Any) -> list[dict]:
    """Normalize a raw ``image_assets`` list to a clean list of dicts."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        storage_path = item.get("storage_path")
        if not isinstance(storage_path, str) or not storage_path.strip():
            continue
        out.append(
            {
                "storage_path": storage_path,
                "thumbnail_path": (
                    item.get("thumbnail_path")
                    if isinstance(item.get("thumbnail_path"), str)
                    else None
                ),
                "caption": item.get("caption") if isinstance(item.get("caption"), str) else None,
                "ocr_text": item.get("ocr_text") if isinstance(item.get("ocr_text"), str) else None,
            }
        )
    return out
