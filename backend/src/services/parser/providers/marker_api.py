"""Marker API client — datalab.to cloud-based document parsing.

Task-based flow:
  1. POST multipart to ``/api/v1/marker`` → receive ``request_check_url``
  2. Poll ``/api/v1/marker/{request_id}`` until status is ``complete``
  3. Extract markdown + metadata from the response

API docs: https://documentation.datalab.to/api-reference/marker
"""

import asyncio
import logging
import re
import uuid
from pathlib import Path

import anyio

from src.core.config import settings

from .._errors import FailureReason, ParserProviderError
from .._http import get_parser_http_client
from .._profile import DocumentProfile
from ..types import PageContent, ParsedDocument, TableAsset

logger = logging.getLogger(__name__)

# Page separator pattern: "\n\n{PAGE_NUMBER}---...---\n\n" (48 dashes per API spec)
_PAGE_SEP_RE = re.compile(r"\n\n\{\d+\}-{48}\n\n")

# Markdown table block matcher
_MARKDOWN_TABLE_RE = re.compile(
    r"(?:\|[^\n]+\|\n\|[-:\s|]+\|\n(?:\|[^\n]+\|\n?)+)",
    re.MULTILINE,
)


def _extract_markdown_tables(markdown: str, page_num: int) -> list[TableAsset]:
    """Parse markdown table blocks into TableAssets."""
    tables: list[TableAsset] = []
    for idx, match in enumerate(_MARKDOWN_TABLE_RE.finditer(markdown)):
        block = match.group(0)
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        # Skip separator line (contains only |, -, :, spaces)
        data_lines = [line for line in lines if not re.fullmatch(r"[\|\-\s:]+", line)]
        if not data_lines:
            continue
        rows: list[tuple[str, ...]] = []
        for line in data_lines:
            cells = [cell.strip() for cell in line.split("|")]
            # Drop empty leading/trailing cells from the outer pipes
            cells = [c for c in cells if c or c == ""]
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]
            if cells:
                rows.append(tuple(cells))
        if rows:
            tables.append(
                TableAsset(
                    table_id=f"p{page_num}_t{idx}",
                    page_num=page_num,
                    rows=tuple(rows),
                    markdown=block.strip(),
                )
            )
    return tables


_MIME_MAP: dict[str, str] = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
    ".html": "text/html",
    ".htm": "text/html",
}


def _get_mime_type(file_path: Path) -> str:
    """Return the MIME type for a file based on its extension."""
    return _MIME_MAP.get(file_path.suffix.lower(), "application/octet-stream")


class MarkerAPIParser:
    """HTTP client for the datalab.to Marker cloud API."""

    async def parse(self, file_path: Path, profile: DocumentProfile) -> ParsedDocument:
        """Parse a document via the Marker API.

        Raises ``ParserProviderError`` on any failure so the cascade can demote.
        """
        api_key = settings.MARKER_API_KEY.get_secret_value()
        if not api_key:
            raise ParserProviderError(
                provider="marker",
                retryable=False,
                reason=FailureReason.AUTH_FAILED,
                cause=Exception("MARKER_API_KEY is missing"),
            )

        base_url = settings.MARKER_BASE_URL.rstrip("/")
        client = get_parser_http_client()
        headers = {"X-API-Key": api_key}

        try:
            # Step 1: Submit file
            mime_type = _get_mime_type(file_path)
            # Use "accurate" mode for scanned/image-heavy docs to ensure OCR
            mode = "accurate" if profile.is_likely_scanned or profile.image_ratio > 0.5 else "fast"
            data = {
                "output_format": "markdown",
                "paginate": "true",
                "mode": mode,
            }
            boundary = f"meeting-agent-{uuid.uuid4().hex}"
            safe_filename = file_path.name.replace('"', "_").replace("\\", "_")

            async def _multipart_body():
                for field_name, value in data.items():
                    yield (
                        f"--{boundary}\r\n"
                        f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'
                        f"{value}\r\n"
                    ).encode()
                yield (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode()
                async with await anyio.open_file(file_path, "rb") as handle:
                    while chunk := await handle.read(1024 * 1024):
                        yield chunk
                yield f"\r\n--{boundary}--\r\n".encode()

            resp = await client.post(
                base_url,
                content=_multipart_body(),
                headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
            )

            if resp.status_code in (401, 403):
                logger.error(
                    "Marker API key invalid or revoked (HTTP %d). "
                    "Please check your MARKER_API_KEY configuration.",
                    resp.status_code,
                )
                raise ParserProviderError(
                    provider="marker",
                    retryable=False,
                    reason=FailureReason.AUTH_FAILED,
                    cause=Exception(
                        f"API key invalid/revoked (HTTP {resp.status_code}) — check MARKER_API_KEY"
                    ),
                )
            if resp.status_code == 429:
                logger.debug("Provider marker HTTP 429 response: %s", resp.text[:500])
                raise ParserProviderError(
                    provider="marker",
                    retryable=False,
                    reason=FailureReason.RATE_LIMITED,
                    cause=Exception("Marker API rate limited (HTTP 429)"),
                )
            if resp.status_code >= 500:
                raise ParserProviderError(
                    provider="marker",
                    retryable=True,
                    cause=Exception(f"Server error {resp.status_code}"),
                )
            if resp.status_code >= 400:
                logger.debug(
                    "Provider marker HTTP %d response: %s",
                    resp.status_code,
                    resp.text[:500],
                )
                raise ParserProviderError(
                    provider="marker",
                    retryable=False,
                    cause=Exception(f"HTTP {resp.status_code} from marker"),
                )

            body = resp.json()
            request_id = body.get("request_id", "unknown")
            # Backward-compatible: some integrations return ``check_url`` while
            # the current API docs use ``request_check_url``.
            check_url = body.get("request_check_url") or body.get("check_url")

            if not body.get("success", True):
                error_msg = body.get("error", "Unknown error")
                raise ParserProviderError(
                    provider="marker",
                    retryable=False,
                    cause=Exception(f"Submit failed: {error_msg}"),
                )

            if not check_url:
                # Small files may return results directly
                return self._map_response(body, file_path)

            logger.info("Marker task submitted: %s", request_id)

            # Step 2: Poll until done
            poll_interval = getattr(settings, "PARSER_POLL_INTERVAL_SECONDS", 2.0)
            max_wait = settings.MARKER_MAX_WAIT_SECONDS
            result = await self._poll(
                client, check_url, headers, poll_interval, max_wait, request_id
            )
            return self._map_response(result, file_path)

        except ParserProviderError:
            raise
        except Exception as exc:
            raise ParserProviderError(provider="marker", retryable=True, cause=exc) from exc

    async def _poll(
        self,
        client,
        check_url: str,
        headers: dict,
        interval: float,
        max_wait: int,
        request_id: str,
    ) -> dict:
        """Poll the check_url until processing completes or times out."""
        import time

        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            resp = await client.get(check_url, headers=headers)

            if resp.status_code >= 500:
                raise ParserProviderError(
                    provider="marker",
                    retryable=True,
                    cause=Exception(f"Poll error {resp.status_code}"),
                )

            body = resp.json()
            status = str(body.get("status", "")).lower()

            if status in {"complete", "completed", "processed", "success", "done"}:
                return body
            if status in {"failed", "error"}:
                error_msg = body.get("error", "Unknown error")
                raise ParserProviderError(
                    provider="marker",
                    retryable=False,
                    reason=FailureReason.UNKNOWN,
                    cause=Exception(f"Task {request_id} failed: {error_msg}"),
                )

            # Some responses omit ``status`` but already contain final payload.
            if not status and (
                body.get("markdown") is not None or body.get("metadata") is not None
            ):
                return body

            await asyncio.sleep(interval)

        raise ParserProviderError(
            provider="marker",
            retryable=True,
            reason=FailureReason.NETWORK_TIMEOUT,
            cause=TimeoutError(f"Task {request_id} timed out after {max_wait}s"),
        )

    def _map_response(self, body: dict, file_path: Path) -> ParsedDocument:
        """Map the Marker API response into a ParsedDocument."""
        markdown = body.get("markdown", "") or ""
        metadata = body.get("metadata") or {}
        page_count = body.get("page_count") or metadata.get("page_count", 1)
        images = body.get("images") or {}

        # Split by page separator pattern when paginated
        page_texts = self._split_pages(markdown, page_count)

        pages: list[PageContent] = []
        for i in range(page_count):
            page_text = page_texts[i].strip() if i < len(page_texts) else ""
            page_images = images.get(str(i), []) if isinstance(images, dict) else []
            table_assets = tuple(_extract_markdown_tables(page_text, i + 1))
            pages.append(
                PageContent(
                    page_num=i + 1,
                    text=page_text,
                    images=[{"type": "image", "data": img} for img in page_images] or None,
                    metadata={"has_images": len(page_images) > 0},
                    table_assets=table_assets,
                )
            )

        return ParsedDocument(
            file_type=file_path.suffix.lower().lstrip("."),
            pages=pages,
            metadata={
                **{k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))},
                "parser": "marker",
            },
            total_pages=page_count,
        )

    def _split_pages(self, markdown: str, page_count: int) -> list[str]:
        """Split markdown into per-page text.

        The Marker API paginates with a pattern like:
        ``\\n\\n{PAGE_NUMBER}------------------------------------------------\\n\\n``
        The first separator creates a leading empty string which we strip.
        """
        if not markdown:
            return [""] * max(page_count, 1)

        parts = _PAGE_SEP_RE.split(markdown)

        # Strip leading empty string from the first separator
        if parts and not parts[0].strip():
            parts = parts[1:]

        if len(parts) >= page_count:
            return parts[:page_count]

        # Not enough parts from separator — even-split fallback
        lines = markdown.split("\n")
        lines_per_page = max(len(lines) // max(page_count, 1), 1)
        return [
            "\n".join(lines[i * lines_per_page : (i + 1) * lines_per_page])
            for i in range(page_count)
        ]
