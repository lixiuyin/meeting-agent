"""MinerU API client — mineru.net v4 cloud document extraction.

Implements the documented v4 batch-upload flow
(https://mineru.net/apiManage/docs):

  1. ``POST /api/v4/file-urls/batch`` — request a signed upload URL plus a
     ``batch_id``.
  2. ``PUT`` the file binary directly to that URL (no Content-Type header).
  3. Poll ``GET /api/v4/extract-results/batch/{batch_id}`` until the entry
     reaches ``state == "done"``.
  4. Download ``full_zip_url`` and read ``full.md`` + ``content_list.json``
     out of the result archive.

Inline base64 / multipart uploads are not supported by this API — the
endpoint that historically accepted them rejects with HTTP 413
``file upload not allowed``.
"""

import asyncio
import base64
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

from src.core.config import settings

from .._errors import FailureReason, ParserProviderError
from .._http import get_parser_http_client
from .._profile import DocumentProfile
from ..types import PageContent, ParsedDocument

logger = logging.getLogger(__name__)


# Suffixes the user's MINERU_BASE_URL may legacy-contain. We strip them so
# the same setting keeps working whether it points at the API root or at a
# specific endpoint.
_LEGACY_BASE_SUFFIXES = (
    "/extract/task/batch",
    "/extract-results/batch",
    "/file-urls/batch",
    "/extract/task",
    "/extract",
)

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"})


class MinerUAPIParser:
    """HTTP client for the mineru.net v4 batch extract API."""

    @staticmethod
    def _api_root() -> str:
        """Return the v4 API root, accepting historical legacy URLs."""
        base = (settings.MINERU_BASE_URL or "").rstrip("/")
        for suffix in _LEGACY_BASE_SUFFIXES:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return base.rstrip("/")

    @staticmethod
    def _raise_http_error(resp: Any, *, context: str) -> None:
        """Translate provider HTTP failures to ``ParserProviderError``."""
        if resp.status_code in (401, 403):
            logger.error(
                "MinerU API key invalid or revoked (HTTP %d) at %s — check MINERU_API_KEY",
                resp.status_code,
                context,
            )
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                reason=FailureReason.AUTH_FAILED,
                cause=Exception(f"API key invalid/revoked (HTTP {resp.status_code}) at {context}"),
            )
        if resp.status_code == 429:
            logger.debug("Provider mineru HTTP 429 at %s: %s", context, resp.text[:500])
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                reason=FailureReason.RATE_LIMITED,
                cause=Exception(f"MinerU rate limited (HTTP 429) at {context}"),
            )
        if resp.status_code >= 500:
            raise ParserProviderError(
                provider="mineru",
                retryable=True,
                cause=Exception(f"Server error {resp.status_code} at {context}"),
            )
        if resp.status_code >= 400:
            logger.debug(
                "Provider mineru HTTP %d at %s: %s",
                resp.status_code,
                context,
                resp.text[:500],
            )
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                cause=Exception(f"HTTP {resp.status_code} from mineru at {context}"),
            )

    @staticmethod
    def _check_api_code(body: dict, *, context: str) -> None:
        """Reject API-level error envelopes with ``code != 0``."""
        code = body.get("code")
        if isinstance(code, int) and code != 0:
            msg = body.get("msg") or body.get("message") or "unknown"
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                cause=Exception(f"MinerU API error at {context}: code={code} msg={msg}"),
            )

    async def parse(self, file_path: Path, profile: DocumentProfile) -> ParsedDocument:
        """Parse a document via the MinerU v4 batch API.

        Raises ``ParserProviderError`` on any failure.
        """
        api_key = settings.MINERU_API_KEY.get_secret_value()
        if not api_key:
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                reason=FailureReason.AUTH_FAILED,
                cause=Exception("MINERU_API_KEY is missing"),
            )

        api_root = self._api_root()
        if not api_root:
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                cause=Exception("MINERU_BASE_URL is not configured"),
            )

        client = get_parser_http_client()
        is_ocr = profile.is_likely_scanned or profile.avg_chars_per_page < 50
        auth_headers = {"Authorization": f"Bearer {api_key}"}

        try:
            batch_id, upload_url = await self._request_upload_url(
                client, api_root, auth_headers, file_path, is_ocr
            )
            await self._upload_file(client, upload_url, file_path)
            logger.info(
                "MinerU batch task created: %s (file=%s ocr=%s)",
                batch_id,
                file_path.name,
                is_ocr,
            )
            entry = await self._poll_batch(client, api_root, auth_headers, batch_id, file_path.name)
            return await self._download_and_build(client, entry, file_path)
        except ParserProviderError:
            raise
        except Exception as exc:
            raise ParserProviderError(provider="mineru", retryable=True, cause=exc) from exc

    # ── Step 1: request signed upload URL ──────────────────────────────

    async def _request_upload_url(
        self,
        client: Any,
        api_root: str,
        auth_headers: dict[str, str],
        file_path: Path,
        is_ocr: bool,
    ) -> tuple[str, str]:
        """Call ``/file-urls/batch`` and return ``(batch_id, upload_url)``."""
        endpoint = f"{api_root}/file-urls/batch"
        payload = {
            "files": [{"name": file_path.name, "is_ocr": is_ocr}],
            "enable_formula": True,
            "enable_table": True,
            "language": "auto",
        }
        resp = await client.post(endpoint, json=payload, headers=auth_headers)
        self._raise_http_error(resp, context="file-urls/batch")
        body = resp.json()
        self._check_api_code(body, context="file-urls/batch")

        data = body.get("data") or {}
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls") or []
        if not batch_id or not file_urls:
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                cause=Exception(f"file-urls/batch missing batch_id/file_urls: {data}"),
            )
        return str(batch_id), str(file_urls[0])

    # ── Step 2: upload file binary to signed URL ───────────────────────

    async def _upload_file(self, client: Any, upload_url: str, file_path: Path) -> None:
        """PUT the file bytes to the pre-signed URL.

        Per MinerU docs the request must NOT carry a ``Content-Type`` header;
        httpx adds one for ``content=`` payloads, so we pass an empty header
        explicitly to suppress it.
        """
        body = await asyncio.to_thread(file_path.read_bytes)
        resp = await client.put(upload_url, content=body, headers={"Content-Type": ""})
        if resp.status_code >= 400:
            raise ParserProviderError(
                provider="mineru",
                retryable=resp.status_code >= 500,
                cause=Exception(f"file upload failed (HTTP {resp.status_code}): {resp.text[:300]}"),
            )

    # ── Step 3: poll batch results ─────────────────────────────────────

    async def _poll_batch(
        self,
        client: Any,
        api_root: str,
        auth_headers: dict[str, str],
        batch_id: str,
        file_name: str,
    ) -> dict:
        """Poll the batch endpoint until our file's entry reaches ``done``."""
        import time

        poll_url = f"{api_root}/extract-results/batch/{batch_id}"
        interval = float(getattr(settings, "PARSER_POLL_INTERVAL_SECONDS", 2.0))
        deadline = time.monotonic() + settings.MINERU_MAX_WAIT_SECONDS

        while time.monotonic() < deadline:
            resp = await client.get(poll_url, headers=auth_headers)
            if resp.status_code >= 500:
                raise ParserProviderError(
                    provider="mineru",
                    retryable=True,
                    cause=Exception(f"Poll error {resp.status_code}"),
                )
            if resp.status_code >= 400:
                self._raise_http_error(resp, context="extract-results/batch")
            body = resp.json()
            self._check_api_code(body, context="extract-results/batch")

            entry = self._select_entry(body, file_name)
            if entry is None:
                await asyncio.sleep(interval)
                continue
            state = str(entry.get("state", "")).lower()
            if state == "done":
                return entry
            if state == "failed":
                err = entry.get("err_msg") or "unknown error"
                raise ParserProviderError(
                    provider="mineru",
                    retryable=False,
                    cause=Exception(f"Batch {batch_id} failed: {err}"),
                )
            await asyncio.sleep(interval)

        raise ParserProviderError(
            provider="mineru",
            retryable=True,
            reason=FailureReason.NETWORK_TIMEOUT,
            cause=TimeoutError(
                f"Batch {batch_id} timed out after {settings.MINERU_MAX_WAIT_SECONDS}s"
            ),
        )

    @staticmethod
    def _select_entry(body: dict, file_name: str) -> dict | None:
        """Pick our file's result entry from the batch response."""
        results = (body.get("data") or {}).get("extract_result") or []
        if not results:
            return None
        for item in results:
            if isinstance(item, dict) and item.get("file_name") == file_name:
                return item
        first = results[0]
        return first if isinstance(first, dict) else None

    # ── Step 4: download zip and build ParsedDocument ──────────────────

    async def _download_and_build(
        self, client: Any, entry: dict, file_path: Path
    ) -> ParsedDocument:
        full_zip_url = entry.get("full_zip_url")
        if not full_zip_url:
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                cause=Exception(f"Batch result has no full_zip_url: {entry}"),
            )
        resp = await client.get(full_zip_url)
        if resp.status_code >= 400:
            raise ParserProviderError(
                provider="mineru",
                retryable=resp.status_code >= 500,
                cause=Exception(f"Result download failed (HTTP {resp.status_code})"),
            )
        markdown, content_list = self._unpack_zip(resp.content)
        return self._build_parsed_doc(markdown, content_list, entry, file_path)

    @staticmethod
    def _unpack_zip(zip_bytes: bytes) -> tuple[str, list]:
        """Pull markdown + content_list.json out of the result archive."""
        markdown = ""
        content_list: list = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                lower = name.lower()
                if not markdown and (
                    lower.endswith("/full.md") or lower == "full.md" or lower.endswith(".md")
                ):
                    markdown = zf.read(name).decode("utf-8", errors="replace")
                if not content_list and lower.endswith("content_list.json"):
                    try:
                        parsed = json.loads(zf.read(name).decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        parsed = []
                    if isinstance(parsed, list):
                        content_list = parsed
        return markdown, content_list

    def _build_parsed_doc(
        self,
        markdown: str,
        content_list: list,
        entry: dict,
        file_path: Path,
    ) -> ParsedDocument:
        if content_list:
            pages = self._map_content_list(content_list)
        elif markdown:
            pages = self._split_markdown(markdown)
        else:
            pages = [PageContent(page_num=1, text="", metadata={"parser": "mineru"})]

        if settings.RAG_INDEX_IMAGE_CAPTIONS:
            pages = self._attach_image_fallbacks(pages, file_path)

        return ParsedDocument(
            file_type=file_path.suffix.lower().lstrip("."),
            pages=pages,
            metadata={
                "parser": "mineru",
                "batch_state": entry.get("state"),
                "data_id": entry.get("data_id"),
            },
            total_pages=len(pages),
        )

    # ── Result decoding helpers (kept from the previous implementation) ─

    def _map_content_list(self, content_list: list) -> list[PageContent]:
        """Map MinerU content_list to PageContent items (page_no is 0-indexed)."""
        from collections import defaultdict

        page_texts: dict[int, list[str]] = defaultdict(list)
        page_tables: dict[int, list[list[list[str]]]] = defaultdict(list)

        for item in content_list:
            if not isinstance(item, dict):
                continue
            page_num = int(item.get("page_no", 0)) + 1
            item_type = item.get("type", "text")
            text = item.get("text", "")

            if item_type == "table":
                rows = item.get("rows", [])
                if rows:
                    tbl = [[cell.get("text", "") for cell in row.get("cells", [])] for row in rows]
                    page_tables[page_num].append(tbl)
                    text = "[TABLE]\n" + "\n".join(" | ".join(r) for r in tbl)
                elif text:
                    text = f"[TABLE]\n{text}"
            elif item_type == "title":
                text = f"# {text}"
            elif item_type == "image":
                if text:
                    text = f"[IMAGE OCR]\n{text}"
                else:
                    continue

            if text:
                page_texts[page_num].append(text)

        pages: list[PageContent] = []
        for page_num in sorted(page_texts.keys()):
            pages.append(
                PageContent(
                    page_num=page_num,
                    text="\n\n".join(page_texts[page_num]),
                    tables=page_tables[page_num] or None,
                    metadata={"parser": "mineru"},
                )
            )
        return pages or [PageContent(page_num=1, text="", metadata={"parser": "mineru"})]

    def _split_markdown(self, markdown: str) -> list[PageContent]:
        """Split markdown text into pages by form-feed page breaks."""
        parts = markdown.split("\f") if "\f" in markdown else [markdown]
        return [
            PageContent(
                page_num=i + 1,
                text=part.strip(),
                metadata={"parser": "mineru"},
            )
            for i, part in enumerate(parts)
            if part.strip()
        ]

    def _attach_image_fallbacks(
        self, pages: list[PageContent], file_path: Path
    ) -> list[PageContent]:
        """Attach raw image bytes so downstream VLM captioning has visuals."""
        suffix = file_path.suffix.lower()

        if suffix in _IMAGE_EXTS:
            if not pages:
                return pages
            try:
                image_b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
                return [
                    PageContent(
                        page_num=pages[0].page_num,
                        text=pages[0].text,
                        tables=pages[0].tables,
                        metadata=pages[0].metadata,
                        heading_path=pages[0].heading_path,
                        image_assets=pages[0].image_assets,
                        table_assets=pages[0].table_assets,
                        confidence=pages[0].confidence,
                        images=[{"name": file_path.name, "data": image_b64}],
                    )
                ]
            except Exception:
                logger.debug(
                    "Failed to read image bytes for %s",
                    file_path.name,
                    exc_info=True,
                )
                return pages

        if suffix == ".pdf":
            try:
                import fitz  # PyMuPDF — only used for asset attachment

                with fitz.open(str(file_path)) as doc:
                    rendered: dict[int, str] = {}
                    for idx in range(len(doc)):
                        if idx >= len(pages):
                            break
                        pix = doc[idx].get_pixmap(matrix=fitz.Matrix(2, 2))
                        rendered[idx] = base64.b64encode(pix.tobytes("png")).decode("ascii")

                new_pages: list[PageContent] = []
                for idx, pc in enumerate(pages):
                    img_b64 = rendered.get(idx)
                    new_pages.append(
                        PageContent(
                            page_num=pc.page_num,
                            text=pc.text,
                            tables=pc.tables,
                            metadata=pc.metadata,
                            heading_path=pc.heading_path,
                            image_assets=pc.image_assets,
                            table_assets=pc.table_assets,
                            confidence=pc.confidence,
                            images=[
                                {
                                    "name": f"page-{pc.page_num}.png",
                                    "data": img_b64,
                                }
                            ]
                            if img_b64
                            else pc.images,
                        )
                    )
                return new_pages
            except Exception:
                logger.debug(
                    "Failed to render PDF pages for %s",
                    file_path.name,
                    exc_info=True,
                )
                return pages

        return pages
