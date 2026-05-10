"""Extraction helpers for parsed-page indexing."""

from typing import Any


def _page_extra_meta(page: Any) -> dict[str, Any]:
    """Build page-level metadata while avoiding unsupported empty list values."""
    meta: dict[str, Any] = {}
    heading_path = [str(x) for x in page.heading_path if str(x).strip()]
    if heading_path:
        meta["heading_path"] = heading_path
    if page.confidence is not None:
        meta["confidence"] = float(page.confidence)
    return meta


def _normalize_table_markdown(table: Any) -> str:
    """Convert table payloads into a stable markdown-like string."""
    if table is None:
        return ""
    if isinstance(table, str):
        return table.strip()
    markdown = getattr(table, "markdown", "")
    if isinstance(markdown, str) and markdown.strip():
        return markdown.strip()
    rows = getattr(table, "rows", None)
    if rows is None and isinstance(table, list):
        rows = table
    if not isinstance(rows, (list, tuple)):
        return ""
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        cols = [str(col).strip() for col in row]
        if cols:
            lines.append("| " + " | ".join(cols) + " |")
    return "\n".join(lines).strip()


def _extract_image_texts(
    image_payload: Any,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract caption/OCR and optional asset paths from dict/dataclass payloads."""
    caption: str | None = None
    ocr_text: str | None = None
    storage_path: str | None = None
    thumbnail_path: str | None = None
    if isinstance(image_payload, dict):
        cap = image_payload.get("caption")
        ocr = image_payload.get("ocr_text")
        storage = image_payload.get("storage_path")
        thumb = image_payload.get("thumbnail_path")
    else:
        cap = getattr(image_payload, "caption", None)
        ocr = getattr(image_payload, "ocr_text", None)
        storage = getattr(image_payload, "storage_path", None)
        thumb = getattr(image_payload, "thumbnail_path", None)
    if isinstance(cap, str) and cap.strip():
        caption = cap.strip()
    if isinstance(ocr, str) and ocr.strip():
        ocr_text = ocr.strip()
    if isinstance(storage, str) and storage.strip():
        storage_path = storage.strip()
    if isinstance(thumb, str) and thumb.strip():
        thumbnail_path = thumb.strip()
    return caption, ocr_text, storage_path, thumbnail_path


def _page_preview_paths(page: Any) -> tuple[str | None, str | None]:
    """Extract a page-level preview image path from page assets when available."""
    image_assets = list(getattr(page, "image_assets", ()) or ())
    for asset in image_assets:
        storage = getattr(asset, "storage_path", None)
        thumb = getattr(asset, "thumbnail_path", None)
        storage_path = storage.strip() if isinstance(storage, str) and storage.strip() else None
        thumb_path = thumb.strip() if isinstance(thumb, str) and thumb.strip() else None
        if storage_path or thumb_path:
            return storage_path, thumb_path

    legacy_images = list(getattr(page, "images", ()) or ())
    for image in legacy_images:
        if not isinstance(image, dict):
            continue
        storage = image.get("storage_path") or image.get("path") or image.get("file_path")
        thumb = image.get("thumbnail_path")
        storage_path = storage.strip() if isinstance(storage, str) and storage.strip() else None
        thumb_path = thumb.strip() if isinstance(thumb, str) and thumb.strip() else None
        if storage_path or thumb_path:
            return storage_path, thumb_path

    return None, None
