"""Parser cascade — content-aware routing with cloud APIs.

Flow:
  1. Format dispatch (text/json/csv → local, images → router, pdf/pptx → router)
  2. Local pre-analysis (PyMuPDF / python-pptx) → DocumentProfile
  3. Router selects primary + ordered fallbacks
  4. Each provider tried in order; first quality-passing result wins
  5. All cloud APIs fail → AllParsersFailedError
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import time
from pathlib import Path

from src.core.config import settings
from src.core.trace import TraceContext

from ._errors import _MAX_RETRIES, AllParsersFailedError, FailureReason, ParserProviderError
from ._http import close_parser_http_client_for_current_loop
from ._profile import profile_document
from ._quality import assess_quality
from ._router import ParserName, select_parsers
from .converters import (
    LibreOfficeMissingError,
    _convert_doc_to_docx,
    _convert_ppt_to_pdf,
    _convert_pptx_to_pdf,
    _convert_xls_to_xlsx,
)
from .providers.marker_api import MarkerAPIParser
from .providers.mineru_api import MinerUAPIParser
from .providers.paddle_api import PaddleOCRAPIParser
from .text_parsers import _parse_text_file
from .types import SUPPORTED_EXTS, PageContent, ParsedDocument

logger = logging.getLogger(__name__)

# Module-level executor reused across all parse requests.
_PARSER_LOOP_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="parser-cascade"
)

# ── Provider instances (stateless, safe to reuse) ───────────────────────────

_marker_parser = MarkerAPIParser()
_mineru_parser = MinerUAPIParser()
_paddle_parser = PaddleOCRAPIParser()

_PROVIDERS: dict[ParserName, object] = {
    "marker": _marker_parser,
    "mineru": _mineru_parser,
    "paddle": _paddle_parser,
}


def parse(file_path: Path, trace: TraceContext | None = None) -> str:
    """Parse a document and return plain text."""
    return parse_structured(file_path, trace=trace).to_text()


def parse_structured(
    file_path: Path,
    trace: TraceContext | None = None,
) -> ParsedDocument:
    """Parse a document and return a structured result.

    Uses content-aware routing to pick the best provider for each file.
    All async cloud provider calls run inside a **single** ``asyncio.run()``
    so the shared httpx client is not invalidated between provider attempts.
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix not in SUPPORTED_EXTS:
        raise ValueError(f"Unsupported document format: {suffix}")

    # ── Plain text files — local, no cloud ───────────────────────────────
    if suffix in {".txt", ".md", ".markdown", ".html", ".htm", ".json", ".xml", ".rtf", ".csv"}:
        if trace:
            trace.start_span("parse", "extract", parser="text_fallback")
        result = _parse_text_file(file_path)
        if trace:
            trace.finish_span("parse")
        return result

    # ── Page-count cap ───────────────────────────────────────────────────
    page_count = _estimate_page_count(file_path, suffix)
    if page_count and page_count > settings.MAX_PARSE_PAGES:
        raise ValueError(
            f"Document has {page_count} pages, exceeding the maximum of {settings.MAX_PARSE_PAGES}"
        )

    if trace:
        trace.start_span("parse", "extract")

    # ── Legacy format conversion ─────────────────────────────────────────
    original_suffix = suffix
    converted_path: Path | None = None
    try:
        converted_path, file_path, suffix = _convert_legacy(file_path, suffix)
    except LibreOfficeMissingError:
        logger.warning(
            "LibreOffice not installed; falling back to native parser path",
            extra={"file": str(file_path)},
        )
    except Exception as exc:
        raise ParserProviderError(
            provider="legacy_convert",
            retryable=False,
            cause=exc,
            reason=FailureReason.CONVERSION_FAILED,
        ) from exc

    try:
        # ── Profile the document ─────────────────────────────────────────
        profile = profile_document(file_path)
        logger.info(
            "Document profile: %s pages=%d avg_chars=%.0f img_ratio=%.2f scanned=%s",
            file_path.name,
            profile.page_count,
            profile.avg_chars_per_page,
            profile.image_ratio,
            profile.is_likely_scanned,
        )

        # ── Route to providers ───────────────────────────────────────────
        user_hint = settings.OCR_PROVIDER.lower() if settings.OCR_PROVIDER else ""
        parser_order = select_parsers(profile, user_hint=user_hint)
        logger.info("Parser routing: %s (hint=%s)", parser_order, user_hint or "none")

        # ── Run cascade (all providers in one event-loop) ────────────────
        result = _dispatch_cascade(
            parser_order, file_path, profile, original_suffix=original_suffix
        )

        if trace:
            trace.spans[-1].metadata["parser_used"] = result.metadata.get("parser", "unknown")
            trace.finish_span("parse")

        return result

    except (AllParsersFailedError, RuntimeError):
        if trace:
            trace.finish_span("parse", "error")
        raise
    except Exception:
        if trace:
            trace.finish_span("parse", "error")
        raise
    finally:
        if converted_path:
            with contextlib.suppress(OSError):
                converted_path.unlink()
            with contextlib.suppress(OSError):
                converted_path.parent.rmdir()


# ── Cascade dispatch ─────────────────────────────────────────────────────────


def _dispatch_cascade(
    parser_order: tuple[ParserName, ...],
    file_path: Path,
    profile,
    *,
    original_suffix: str = "",
) -> ParsedDocument:
    """Dispatch the cascade — all providers in a single event-loop pass.

    This avoids the bug where ``asyncio.run()`` closes the loop after each
    provider attempt, invalidating the shared httpx client.
    """
    coro = _cascade_async(parser_order, file_path, profile, original_suffix=original_suffix)

    async def _run_with_cleanup() -> ParsedDocument:
        try:
            return await coro
        finally:
            await close_parser_http_client_for_current_loop()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Called from inside FastAPI's event loop — offload to the shared executor
        future = _PARSER_LOOP_EXECUTOR.submit(asyncio.run, _run_with_cleanup())
        return future.result(timeout=settings.PARSE_TIMEOUT_SECONDS)
    else:
        return asyncio.run(_run_with_cleanup())


async def _cascade_async(
    parser_order: tuple[ParserName, ...],
    file_path: Path,
    profile,
    *,
    original_suffix: str = "",
) -> ParsedDocument:
    """Try providers sequentially inside a single event loop.

    Cloud providers (marker, mineru, paddle) use the shared httpx client
    which is bound to this loop.  After each provider returns, the output
    is checked by ``assess_quality()`` — poor-quality results trigger
    fallback to the next provider.
    """
    deadline = time.monotonic() + settings.PARSE_TIMEOUT_SECONDS
    errors: list[ParserProviderError] = []
    fallback_from: list[str] = []

    for parser_name in parser_order:
        if time.monotonic() > deadline:
            logger.warning("Parse timeout budget exhausted for %s", file_path.name)
            break

        provider = _PROVIDERS[parser_name]
        retries_left = _MAX_RETRIES

        while retries_left >= 0:
            if time.monotonic() > deadline:
                logger.warning("Parse timeout budget exhausted for %s", file_path.name)
                break

            try:
                result = await provider.parse(file_path, profile)  # type: ignore[union-attr]

                # ── Quality gate ──────────────────────────────────────────────
                quality = assess_quality(result, profile)
                if not quality.is_satisfactory:
                    logger.warning(
                        "parser=%s file=%s event=quality_failed quality=%.1f reasons=%s",
                        parser_name,
                        file_path.name,
                        quality.score,
                        ";".join(quality.reasons),
                    )
                    fallback_from.append(parser_name)
                    errors.append(
                        ParserProviderError(
                            provider=parser_name,
                            retryable=False,
                            cause=ValueError(f"Quality failed: {quality.reasons}"),
                            reason=FailureReason.QUALITY_GATE,
                        )
                    )
                    break

                result = _annotate_metadata(
                    result, parser_name, fallback_from, original_suffix=original_suffix
                )
                logger.info(
                    "parser=%s file=%s event=parse_ok quality=%.1f fallback_from=%s",
                    parser_name,
                    file_path.name,
                    quality.score,
                    fallback_from or [],
                )
                return result

            except ParserProviderError as ppe:
                if ppe.retryable and retries_left > 0:
                    retries_left -= 1
                    logger.warning(
                        "Parser %s failed for %s (retryable, %d retries left) — retrying",
                        ppe.provider,
                        file_path.name,
                        retries_left,
                    )
                    continue

                errors.append(ppe)
                fallback_from.append(ppe.provider)
                logger.warning(
                    "parser=%s file=%s event=provider_failed reason=%s detail=%s",
                    ppe.provider,
                    file_path.name,
                    ppe.reason.value,
                    str(ppe.cause) if ppe.cause else "n/a",
                )
                break

    if errors:
        # M-2: Last-resort fallback to local PyMuPDF before raising a hard
        # failure, consistent with project documentation.
        if original_suffix == ".pdf":
            try:
                import fitz

                text_parts: list[str] = []
                with fitz.open(str(file_path)) as doc:
                    for page in doc:
                        text_parts.append(page.get_text("text"))  # type: ignore[arg-type]
                fallback_text = "\n\n".join(text_parts).strip()
                if fallback_text:
                    logger.warning(
                        "All cloud parsers failed for %s — falling back to "
                        "local PyMuPDF text extraction",
                        file_path.name,
                    )
                    return ParsedDocument(
                        file_type="pdf",
                        pages=[PageContent(page_num=1, text=fallback_text)],
                        metadata={"parser": "local_pymupdf_fallback"},
                        total_pages=1,
                        parser_used="local_pymupdf_fallback",
                    )
            except Exception:
                logger.warning(
                    "Local PyMuPDF fallback also failed for %s",
                    file_path.name,
                    exc_info=True,
                )
        raise AllParsersFailedError(errors)
    raise RuntimeError(f"No suitable parser found for {file_path.name}")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _convert_legacy(file_path: Path, suffix: str) -> tuple[Path | None, Path, str]:
    """Convert legacy formats (.ppt, .pptx, .doc, .xls) to modern equivalents.

    .ppt/.pptx are converted to PDF for a unified cascade path, falling back
    to the legacy .ppt→.pptx conversion when LibreOffice is unavailable.
    """
    converted_path: Path | None = None

    if suffix == ".ppt":
        try:
            logger.info("Converting .ppt to .pdf: %s", file_path)
            converted_path = _convert_ppt_to_pdf(file_path)
            return converted_path, converted_path, ".pdf"
        except Exception:
            logger.warning("All .ppt→.pdf conversion methods failed", exc_info=True)

    if suffix == ".pptx":
        try:
            logger.info("Converting .pptx to .pdf: %s", file_path)
            converted_path = _convert_pptx_to_pdf(file_path)
            return converted_path, converted_path, ".pdf"
        except Exception:
            logger.warning("All .pptx→.pdf conversion methods failed", exc_info=True)

    if suffix == ".doc":
        try:
            logger.info("Converting .doc to .docx: %s", file_path)
            converted_path = _convert_doc_to_docx(file_path)
            return converted_path, converted_path, ".docx"
        except LibreOfficeMissingError:
            logger.warning(
                "Skipping .doc→.docx conversion (LibreOffice unavailable). "
                "Attempting direct parse — results may be limited."
            )

    if suffix == ".xls":
        try:
            logger.info("Converting .xls to .xlsx: %s", file_path)
            converted_path = _convert_xls_to_xlsx(file_path)
            return converted_path, converted_path, ".xlsx"
        except LibreOfficeMissingError:
            logger.warning(
                "Skipping .xls→.xlsx conversion (LibreOffice unavailable). "
                "Attempting direct parse — results may be limited."
            )

    return converted_path, file_path, suffix


def _annotate_metadata(
    result: ParsedDocument,
    parser_name: str,
    fallback_from: list[str],
    *,
    original_suffix: str = "",
) -> ParsedDocument:
    """Add parser audit info to the result metadata (immutable update)."""
    new_metadata = {
        **result.metadata,
        "parser": parser_name,
    }
    if fallback_from:
        new_metadata["fallback_from"] = list(fallback_from)
    if original_suffix in (".ppt", ".pptx"):
        new_metadata["original_format"] = original_suffix.lstrip(".")
    return ParsedDocument(
        file_type=result.file_type,
        pages=result.pages,
        metadata=new_metadata,
        total_pages=result.total_pages,
        parser_used=parser_name,
        fallback_from=tuple(fallback_from),
        language=result.language,
    )


def _estimate_page_count(file_path: Path, suffix: str) -> int:
    """Best-effort page/slide count for parser limits."""
    if suffix == ".pdf":
        try:
            import fitz

            with fitz.open(str(file_path)) as doc:
                return len(doc)
        except Exception:
            logger.debug("Failed to estimate PDF page count: %s", file_path.name, exc_info=True)
    elif suffix == ".pptx":
        try:
            from pptx import Presentation

            prs = Presentation(str(file_path))
            return len(prs.slides)
        except Exception:
            logger.debug("Failed to estimate PPTX slide count: %s", file_path.name, exc_info=True)
    elif suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"}:
        return 1
    elif suffix in {".txt", ".md", ".markdown", ".html", ".htm", ".json", ".xml", ".rtf", ".csv"}:
        try:
            line_count = len(file_path.read_text(encoding="utf-8", errors="ignore").splitlines())
            return max(1, line_count // 50)
        except Exception:
            logger.debug("Failed to estimate text page count: %s", file_path.name, exc_info=True)
    return 0
