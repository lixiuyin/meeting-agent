"""Image captioning helpers backed by an OpenAI-compatible vision endpoint."""

from __future__ import annotations

import asyncio
import base64
import collections
import contextlib
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from ...core.config import settings
from ._client import get_vision_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hash-keyed image cache (LRU, capped at 512 entries)
# ---------------------------------------------------------------------------
_IMAGE_CACHE_MAX = 512
_IMAGE_CACHE: collections.OrderedDict[str, CombinedImageContent] = collections.OrderedDict()
_IMAGE_CACHE_LOCK = threading.Lock()


def _image_hash(image_path: str | Path) -> str:
    """Compute a stable cache key from file path, mtime, and size.

    Uses stat metadata instead of reading the full file content — much cheaper
    for large images while still providing a reliable cache key (mtime + size
    change on any content modification).
    """
    path = Path(image_path)
    if not path.is_absolute():
        path = settings.UPLOAD_DIR / path
    stat = path.stat()
    raw = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_get(key: str) -> CombinedImageContent | None:
    with _IMAGE_CACHE_LOCK:
        if key in _IMAGE_CACHE:
            _IMAGE_CACHE.move_to_end(key)
            return _IMAGE_CACHE[key]
    return None


def _cache_put(key: str, value: CombinedImageContent) -> None:
    with _IMAGE_CACHE_LOCK:
        _IMAGE_CACHE[key] = value
        _IMAGE_CACHE.move_to_end(key)
        while len(_IMAGE_CACHE) > _IMAGE_CACHE_MAX:
            _IMAGE_CACHE.popitem(last=False)


_NOISE_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)
_REPEATED_TOKEN_RE = re.compile(r"^(\S+)(?:\s+\1){2,}$", re.IGNORECASE)
_NO_INFO_PATTERNS = (
    re.compile(r"^(?:n/?a|none|null|empty|unknown|unsure|unreadable)$", re.IGNORECASE),
    re.compile(r"^(?:no|not)\s+(?:text|content|information)\b", re.IGNORECASE),
    re.compile(r"^unable to (?:read|determine|identify|extract)\b", re.IGNORECASE),
    re.compile(r"^cannot (?:read|determine|identify|extract)\b", re.IGNORECASE),
    re.compile(r"^(?:there is|there's)\s+no\s+(?:text|content|information)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ImageCaption:
    """Structured image caption result."""

    caption: str
    confidence: float | None = None
    is_text_bearing: bool | None = None
    language: str | None = None


def _vision_endpoint() -> tuple[str, str | None, str]:
    base_url = settings.VISION_BASE_URL or settings.LLM_BASE_URL or ""
    api_key = (
        settings.VISION_API_KEY.get_secret_value()
        if settings.VISION_API_KEY is not None
        else (settings.LLM_API_KEY.get_secret_value() if settings.LLM_API_KEY is not None else None)
    )
    model = settings.VISION_MODEL or settings.LLM_MODEL
    return base_url.rstrip("/"), api_key, model


def _image_to_data_url(image_path: str | Path) -> str:
    path = Path(image_path)
    if not path.is_absolute():
        path = settings.UPLOAD_DIR / path
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _clean_vision_output(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned.strip("`").strip()
    return re.sub(r"\s+", " ", cleaned).strip() or None


def _is_low_information(text: str) -> bool:
    lowered = text.lower()
    if _NOISE_ONLY_RE.fullmatch(text):
        return True
    if _REPEATED_TOKEN_RE.fullmatch(text):
        return True
    if any(pattern.search(lowered) for pattern in _NO_INFO_PATTERNS):
        return True
    token_count = len(text.split())
    return token_count <= 1 and len(text) < 20


def _gate_vision_output(text: str | None, *, kind: str) -> str | None:
    cleaned = _clean_vision_output(text)
    if not cleaned or _is_low_information(cleaned):
        return None

    if kind == "caption" and len(cleaned) < settings.VISION_CAPTION_MIN_CHARS:
        return None
    if kind == "ocr" and len(cleaned) < settings.VISION_OCR_MIN_CHARS:
        return None
    return cleaned


def is_meaningful_caption(text: str | None) -> bool:
    """Whether caption text is informative enough for indexing."""
    return _gate_vision_output(text, kind="caption") is not None


def is_meaningful_ocr_text(text: str | None) -> bool:
    """Whether OCR text is informative enough for indexing."""
    return _gate_vision_output(text, kind="ocr") is not None


async def _call_vision(
    image_path: str | Path,
    *,
    prompt: str,
    max_tokens: int = 200,
    output_kind: str = "caption",
) -> str | None:
    if not settings.MULTIMODAL_CAPTIONING_ENABLED:
        return None

    base_url, api_key, model = _vision_endpoint()
    if not base_url or not api_key or not model:
        return None

    client = get_vision_client()
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_to_data_url(image_path)}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    max_attempts = max(1, settings.VISION_RETRY_MAX_ATTEMPTS)
    base_delay = max(0.0, settings.VISION_RETRY_BASE_DELAY_SECONDS)
    max_delay = max(base_delay, settings.VISION_RETRY_MAX_DELAY_SECONDS)

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            message = choices[0].get("message") or {}
            return _gate_vision_output(message.get("content"), kind=output_kind)
        except httpx.HTTPStatusError as exc:
            with contextlib.suppress(Exception):
                await exc.response.aread()
            status_code = exc.response.status_code
            retryable = status_code == 429 or status_code >= 500
            if not retryable or attempt >= max_attempts:
                logger.warning(
                    "Vision request failed for %s with status=%d after %d attempt(s)",
                    image_path,
                    status_code,
                    attempt,
                    exc_info=True,
                )
                return None
        except httpx.HTTPError:
            if attempt >= max_attempts:
                logger.warning(
                    "Vision request failed for %s after %d attempt(s)",
                    image_path,
                    attempt,
                    exc_info=True,
                )
                return None

        if base_delay > 0:
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            await asyncio.sleep(delay)
    return None


async def caption_image(image_path: str | Path) -> str | None:
    """Return a concise image caption."""
    return await _call_vision(
        image_path,
        prompt=(
            "Describe this image semantically for retrieval in one concise sentence. "
            "Focus only on visual entities, relations, actions, scene type, and notable objects. "
            "Do not infer from external context."
        ),
        max_tokens=120,
        output_kind="caption",
    )


async def transcribe_text_bearing_image(image_path: str | Path) -> str | None:
    """Extract readable text from an image."""
    return await _call_vision(
        image_path,
        prompt=(
            "Extract all visible text exactly as written. Preserve line breaks where possible. "
            "If no meaningful text is present, reply with an empty response."
        ),
        max_tokens=400,
        output_kind="ocr",
    )


async def describe_image_semantics(image_path: str | Path) -> str | None:
    """Return richer visual semantics for images with weak/empty captions."""
    return await _call_vision(
        image_path,
        prompt=(
            "Provide one sentence of pure visual semantics for retrieval. "
            "Mention key objects, their relationships, spatial layout, and scene context. "
            "Ignore surrounding document text and do not speculate beyond visible content."
        ),
        max_tokens=160,
        output_kind="caption",
    )


@dataclass(frozen=True)
class CombinedImageContent:
    """Result of a single combined VLM call: caption + OCR + semantics."""

    caption: str | None
    ocr_text: str | None
    semantics: str | None


_COMBINED_EXTRACTION_PROMPT = (
    "Analyze the image and return a single JSON object with exactly these keys:\n"
    '  "caption": one concise sentence describing what the image shows '
    "(visual entities, relations, scene type). Empty string if nothing meaningful.\n"
    '  "ocr": all visible text exactly as written, preserving line breaks. '
    "Empty string if no text.\n"
    '  "semantics": one sentence of richer visual semantics — key objects, '
    "their relationships, spatial layout, scene context. Empty string if not applicable.\n\n"
    "Do not speculate beyond visible content. Return JSON only, no code fences."
)


def _parse_combined_vision_json(raw: str | None) -> CombinedImageContent | None:
    """Parse the combined JSON output. Tolerates fenced code blocks."""
    if not raw:
        return None
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = "\n".join(
            line for line in stripped.splitlines() if not line.strip().startswith("```")
        ).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        logger.debug("Combined vision JSON parse failed; falling back to legacy path")
        return None
    if not isinstance(data, dict):
        return None

    def _field(name: str, kind: str) -> str | None:
        value = data.get(name)
        if not isinstance(value, str):
            return None
        return _gate_vision_output(value, kind=kind)

    return CombinedImageContent(
        caption=_field("caption", "caption"),
        ocr_text=_field("ocr", "ocr"),
        semantics=_field("semantics", "caption"),
    )


async def extract_image_content(image_path: str | Path) -> CombinedImageContent | None:
    """Get caption + OCR + semantics from a single VLM call.

    Falls back to None when the combined path is disabled or the VLM output
    cannot be parsed; callers should then use the individual helpers.

    Results are cached by image content hash (LRU, 512 entries) so duplicate
    images across meetings or retries are free.
    """
    if not settings.MULTIMODAL_CAPTIONING_ENABLED:
        return None
    if not settings.VISION_COMBINED_EXTRACTION_ENABLED:
        return None

    # Cache lookup by content hash
    try:
        img_hash = _image_hash(image_path)
        cached = _cache_get(img_hash)
        if cached is not None:
            logger.debug("Image cache hit for %s (hash=%s)", image_path, img_hash)
            return cached
    except OSError:
        # File doesn't exist or unreadable — let the main path handle it
        img_hash = ""

    base_url, api_key, model = _vision_endpoint()
    if not base_url or not api_key or not model:
        return None

    client = get_vision_client()
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _COMBINED_EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": _image_to_data_url(image_path)}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 600,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    max_attempts = max(1, settings.VISION_RETRY_MAX_ATTEMPTS)
    base_delay = max(0.0, settings.VISION_RETRY_BASE_DELAY_SECONDS)
    max_delay = max(base_delay, settings.VISION_RETRY_MAX_DELAY_SECONDS)

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            message = choices[0].get("message") or {}
            content = message.get("content")
            if not isinstance(content, str):
                return None
            parsed = _parse_combined_vision_json(content)
            if parsed is not None and img_hash:
                _cache_put(img_hash, parsed)
            return parsed
        except httpx.HTTPStatusError as exc:
            with contextlib.suppress(Exception):
                await exc.response.aread()
            status_code = exc.response.status_code
            retryable = status_code == 429 or status_code >= 500
            if not retryable or attempt >= max_attempts:
                logger.warning(
                    "Combined vision request failed for %s with status=%d after %d attempt(s)",
                    image_path,
                    status_code,
                    attempt,
                    exc_info=True,
                )
                return None
        except httpx.HTTPError:
            if attempt >= max_attempts:
                logger.warning(
                    "Combined vision request failed for %s after %d attempt(s)",
                    image_path,
                    attempt,
                    exc_info=True,
                )
                return None
        if base_delay > 0:
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            await asyncio.sleep(delay)
    return None
