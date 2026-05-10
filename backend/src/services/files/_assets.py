"""Meeting asset storage helpers for extracted page images."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from ...core.config import settings
from ..parser.types import ImageAsset

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_name(name: str) -> str:
    return _SAFE_NAME_RE.sub("_", name).strip("._") or "asset"


def _ext_from_bytes(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def _asset_dir(meeting_id: int, file_id: int) -> Path:
    return settings.UPLOAD_DIR / "meeting_assets" / str(meeting_id) / str(file_id)


def _thumbnail_for(image_path: Path) -> Path | None:
    try:
        from PIL import Image
    except Exception:
        return None
    thumb_path = image_path.with_suffix(".thumb.webp")
    try:
        with Image.open(image_path) as img:
            img.thumbnail((480, 480))
            img.convert("RGB").save(thumb_path, format="WEBP", quality=80)
    except Exception:
        return None
    return thumb_path


def save_image_bytes(
    *,
    meeting_id: int,
    file_id: int,
    page_num: int,
    image_bytes: bytes,
    source_provider: str,
    image_name: str = "",
) -> ImageAsset:
    """Persist image bytes and return an ImageAsset descriptor."""
    digest = hashlib.sha256(image_bytes).hexdigest()[:16]
    ext = Path(image_name).suffix.lower() if image_name else ""
    if not ext or ext == ".":
        ext = _ext_from_bytes(image_bytes)
    file_stem = _safe_name(Path(image_name).stem) if image_name else "img"
    out_dir = _asset_dir(meeting_id, file_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"p{page_num}_{file_stem}_{digest}{ext}"
    out_path = out_dir / file_name
    if not out_path.exists():
        out_path.write_bytes(image_bytes)
    thumb_path = _thumbnail_for(out_path)
    storage_rel = out_path.relative_to(settings.UPLOAD_DIR).as_posix()
    thumb_rel = thumb_path.relative_to(settings.UPLOAD_DIR).as_posix() if thumb_path else None
    return ImageAsset(
        asset_id=digest,
        page_num=page_num,
        storage_path=storage_rel,
        thumbnail_path=thumb_rel,
        source_provider=source_provider,
    )


def save_image_base64(
    *,
    meeting_id: int,
    file_id: int,
    page_num: int,
    image_b64: str,
    source_provider: str,
    image_name: str = "",
) -> ImageAsset | None:
    """Persist base64-encoded image data and return an ImageAsset descriptor."""
    payload = image_b64
    if "," in payload and payload.split(",", 1)[0].startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(payload)
    except Exception:
        return None
    if not image_bytes:
        return None
    return save_image_bytes(
        meeting_id=meeting_id,
        file_id=file_id,
        page_num=page_num,
        image_bytes=image_bytes,
        source_provider=source_provider,
        image_name=image_name,
    )
