"""Document processor for PDF/PPT/DOC/XLS/CSV."""

import asyncio
import json
import re
from dataclasses import asdict

from ....core.config import settings
from ...files._assets import save_image_base64
from ...parser import parse_structured
from ...parser.types import ImageAsset, PageContent, TableAsset
from ...vision import (
    caption_image,
    deduplicate_caption_ocr,
    describe_image_semantics,
    extract_image_content,
    is_meaningful_caption,
    is_meaningful_ocr_text,
    transcribe_text_bearing_image,
)
from ._types import FileArtefact, ProcessorContext

_PAGE_LABEL_RE = re.compile(
    r"^(?:page|p\.?)\s*(\d{1,5})(?:\s*(?:/|of)\s*(\d{1,5}))?$",
    re.IGNORECASE,
)
_PAGE_LABEL_ZH_RE = re.compile(r"^第?\s*(\d{1,5})\s*页(?:\s*(?:/|of)\s*(\d{1,5}))?$")
_FRACTION_RE = re.compile(r"^(\d{1,4})\s*/\s*(\d{1,4})$")
_DIGITS_ONLY_RE = re.compile(r"^\d{1,4}$")
_DASHED_DIGITS_RE = re.compile(r"^-+\s*(\d{1,4})\s*-+$")
_PUNCT_ONLY_SHORT_RE = re.compile(r"^[^\w\u4e00-\u9fff]{1,3}$")
_BOILERPLATE_HINT_RE = re.compile(
    r"(confidential|all rights reserved|copyright|internal use|page\s+\d+|\d+\s*/\s*\d+)",
    re.IGNORECASE,
)


def _table_markdown(rows: list[list[str]]) -> str:
    return "\n".join("| " + " | ".join(str(c).strip() for c in row) + " |" for row in rows if row)


def _effective_parse_timeout_seconds(file_size_bytes: int) -> int:
    """Scale parse timeout with file size while enforcing a hard cap."""
    file_mb = max(file_size_bytes, 0) / (1024 * 1024)
    extra = int(file_mb * settings.PARSE_TIMEOUT_PER_MB_SECONDS)
    timeout = settings.PARSE_TIMEOUT_SECONDS + extra
    return max(settings.PARSE_TIMEOUT_SECONDS, min(timeout, settings.PARSE_TIMEOUT_MAX_SECONDS))


def _normalize_line_for_repetition(line: str, *, mask_numbers: bool = False) -> str:
    compact = re.sub(r"\s+", " ", line.strip().lower())
    if mask_numbers:
        compact = re.sub(r"\b(?:page|p\.?)\s*\d+(?:\s*(?:/|of)\s*\d+)?\b", "page #", compact)
        compact = re.sub(r"\b\d+\s*/\s*\d+\b", "#/#", compact)
        compact = re.sub(r"\b\d+\b", "#", compact)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", "", compact).strip()


def _is_header_or_footer_line(non_empty_index: int, *, non_empty_total: int) -> bool:
    edge_lines = max(1, settings.DOC_CLEAN_HEADER_FOOTER_MAX_LINES)
    if non_empty_index < edge_lines:
        return True
    return non_empty_index >= (non_empty_total - edge_lines)


def _is_page_marker_line(
    line: str,
    *,
    page_num: int,
    total_pages: int,
    header_or_footer: bool,
    prev_non_empty_line: str,
    next_non_empty_line: str,
) -> bool:
    stripped = line.strip()
    if not stripped:
        return False

    page_match = _PAGE_LABEL_RE.fullmatch(stripped) or _PAGE_LABEL_ZH_RE.fullmatch(stripped)
    if page_match:
        left = int(page_match.group(1))
        right = int(page_match.group(2)) if page_match.group(2) else None
        if left == page_num:
            return True
        if header_or_footer and total_pages > 0 and 1 <= left <= total_pages:
            if right is None:
                return True
            return right == total_pages or right >= left

    fraction = _FRACTION_RE.fullmatch(stripped)
    if fraction:
        left = int(fraction.group(1))
        right = int(fraction.group(2))
        if left == page_num and (total_pages <= 0 or right == total_pages or right >= left):
            return True
        if header_or_footer and total_pages > 0 and 1 <= left <= total_pages:
            return right == total_pages or right >= left

    dashed = _DASHED_DIGITS_RE.fullmatch(stripped)
    if dashed and header_or_footer:
        value = int(dashed.group(1))
        if value == page_num:
            return True
        return total_pages > 0 and 1 <= value <= total_pages

    if _DIGITS_ONLY_RE.fullmatch(stripped):
        if not header_or_footer:
            return False
        if (
            prev_non_empty_line.rstrip().endswith(":")
            and next_non_empty_line
            and not _DIGITS_ONLY_RE.fullmatch(next_non_empty_line.strip())
        ):
            return False
        try:
            value = int(stripped)
        except ValueError:
            return False
        if value == page_num:
            return True
        return total_pages > 0 and 1 <= value <= total_pages
    return False


def _clean_document_page_texts(pages: list[PageContent]) -> list[str]:
    cleaned_pages, _ = _clean_document_page_texts_with_metrics(pages)
    return cleaned_pages


def _clean_document_page_texts_with_metrics(
    pages: list[PageContent],
) -> tuple[list[str], dict[str, int]]:
    if not pages:
        return (
            [],
            {
                "cleaned_line_count": 0,
                "removed_page_marker_count": 0,
                "removed_repetitive_line_count": 0,
            },
        )
    total_pages = len(pages)

    line_page_counts: dict[str, int] = {}
    masked_line_page_counts: dict[str, int] = {}
    for page in pages:
        non_empty_lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        unique_lines: set[str] = set()
        unique_masked_lines: set[str] = set()
        for idx, stripped in enumerate(non_empty_lines):
            if not _is_header_or_footer_line(idx, non_empty_total=len(non_empty_lines)):
                continue
            normalized = _normalize_line_for_repetition(stripped)
            if not normalized:
                continue
            if len(stripped) > settings.DOC_CLEAN_REPETITION_MAX_LINE_LENGTH:
                continue
            unique_lines.add(normalized)
            if _BOILERPLATE_HINT_RE.search(stripped):
                masked = _normalize_line_for_repetition(stripped, mask_numbers=True)
                if masked:
                    unique_masked_lines.add(masked)
        for normalized in unique_lines:
            line_page_counts[normalized] = line_page_counts.get(normalized, 0) + 1
        for normalized in unique_masked_lines:
            masked_line_page_counts[normalized] = masked_line_page_counts.get(normalized, 0) + 1

    min_pages = max(2, settings.DOC_CLEAN_REPETITION_MIN_PAGES)
    min_ratio = settings.DOC_CLEAN_REPETITION_MIN_RATIO

    repetitive_lines = {
        normalized
        for normalized, count in line_page_counts.items()
        if count >= min_pages and (count / total_pages) >= min_ratio
    }
    repetitive_masked_lines = {
        normalized
        for normalized, count in masked_line_page_counts.items()
        if count >= min_pages and (count / total_pages) >= min_ratio
    }

    removed_page_marker_count = 0
    removed_repetitive_line_count = 0
    cleaned_line_count = 0
    cleaned_pages: list[str] = []
    for page in pages:
        non_empty_lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        non_empty_index = 0
        kept: list[str] = []
        for raw in page.text.splitlines():
            stripped = raw.strip()
            if not stripped:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            header_or_footer = _is_header_or_footer_line(
                non_empty_index, non_empty_total=len(non_empty_lines)
            )
            prev_non_empty_line = (
                non_empty_lines[non_empty_index - 1] if non_empty_index > 0 else ""
            )
            next_non_empty_line = (
                non_empty_lines[non_empty_index + 1]
                if non_empty_index + 1 < len(non_empty_lines)
                else ""
            )
            non_empty_index += 1
            if _is_page_marker_line(
                stripped,
                page_num=page.page_num,
                total_pages=total_pages,
                header_or_footer=header_or_footer,
                prev_non_empty_line=prev_non_empty_line,
                next_non_empty_line=next_non_empty_line,
            ):
                removed_page_marker_count += 1
                continue
            if _PUNCT_ONLY_SHORT_RE.fullmatch(stripped):
                continue
            normalized = _normalize_line_for_repetition(stripped)
            masked_normalized = _normalize_line_for_repetition(stripped, mask_numbers=True)
            if normalized in repetitive_lines:
                removed_repetitive_line_count += 1
                continue
            if (
                _BOILERPLATE_HINT_RE.search(stripped)
                and masked_normalized in repetitive_masked_lines
            ):
                removed_repetitive_line_count += 1
                continue
            kept.append(stripped)
        # Trim leading/trailing blank lines and collapse
        while kept and kept[0] == "":
            kept.pop(0)
        while kept and kept[-1] == "":
            kept.pop()
        cleaned_line_count += sum(1 for line in kept if line)
        cleaned_pages.append("\n".join(kept))
    return (
        cleaned_pages,
        {
            "cleaned_line_count": cleaned_line_count,
            "removed_page_marker_count": removed_page_marker_count,
            "removed_repetitive_line_count": removed_repetitive_line_count,
        },
    )


async def _build_page_dict(page: PageContent) -> dict:
    """Backward-compatible page serializer used by existing tests."""
    heading = None
    for line in page.text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) < 120:
            heading = stripped
            break
    image_captions: list[dict] = []
    if settings.MULTIMODAL_CAPTIONING_ENABLED and page.images:
        for image_ref in page.images:
            if not isinstance(image_ref, dict):
                continue
            image_path = (
                image_ref.get("path")
                or image_ref.get("file_path")
                or image_ref.get("image_path")
                or image_ref.get("uri")
            )
            if not image_path:
                continue
            caption = await caption_image(str(image_path))
            if caption:
                image_captions.append({"image_ref": image_ref, "caption": caption})
    return {
        "page_num": page.page_num,
        "text": page.text,
        "tables": page.tables or [],
        "image_refs": page.images or [],
        "image_captions": image_captions,
        "heading": heading,
    }


async def _enrich_image_asset(asset: ImageAsset) -> ImageAsset:
    caption = asset.caption
    ocr_text = asset.ocr_text
    is_text_bearing = asset.is_text_bearing
    if settings.MULTIMODAL_CAPTIONING_ENABLED:
        combined = None
        if settings.VISION_COMBINED_EXTRACTION_ENABLED:
            combined = await extract_image_content(asset.storage_path)

        if combined is not None:
            caption_candidate = combined.caption or combined.semantics or caption
            caption = caption_candidate if is_meaningful_caption(caption_candidate) else None
            ocr_candidate = combined.ocr_text or ocr_text
            ocr_text = ocr_candidate if is_meaningful_ocr_text(ocr_candidate) else None
        else:
            primary_caption = await caption_image(asset.storage_path)
            caption_candidate = primary_caption or caption
            if not is_meaningful_caption(caption_candidate):
                caption_candidate = (
                    await describe_image_semantics(asset.storage_path) or caption_candidate
                )
            caption = caption_candidate if is_meaningful_caption(caption_candidate) else None
            ocr_candidate = await transcribe_text_bearing_image(asset.storage_path) or ocr_text
            ocr_text = ocr_candidate if is_meaningful_ocr_text(ocr_candidate) else None

        caption, ocr_text = await deduplicate_caption_ocr(caption, ocr_text)
        is_text_bearing = bool(ocr_text and len(ocr_text) >= settings.RAG_IMAGE_OCR_MIN_LENGTH)
    return ImageAsset(
        asset_id=asset.asset_id,
        page_num=asset.page_num,
        storage_path=asset.storage_path,
        thumbnail_path=asset.thumbnail_path,
        bbox=asset.bbox,
        caption=caption,
        ocr_text=ocr_text,
        is_text_bearing=is_text_bearing,
        source_provider=asset.source_provider,
    )


async def _build_page(
    page: PageContent, ctx: ProcessorContext
) -> tuple[PageContent, dict, dict[str, int]]:
    heading = None
    for line in page.text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) < 120:
            heading = stripped
            break

    image_assets: list[ImageAsset] = list(page.image_assets or ())
    for image_ref in page.images or []:
        if not isinstance(image_ref, dict):
            continue
        b64 = image_ref.get("data")
        if not isinstance(b64, str) or not b64.strip():
            continue
        saved = save_image_base64(
            meeting_id=ctx.meeting_id,
            file_id=ctx.file_id,
            page_num=page.page_num,
            image_b64=b64,
            source_provider=str((page.metadata or {}).get("parser", "")),
            image_name=str(image_ref.get("name", "")),
        )
        if saved is not None:
            image_assets.append(saved)

    enriched_images = [await _enrich_image_asset(asset) for asset in image_assets]

    table_assets: list[TableAsset] = list(page.table_assets or ())
    for idx, table_rows in enumerate(page.tables or []):
        if not table_rows:
            continue
        row_tuple = tuple(tuple(str(c) for c in row) for row in table_rows)
        table_assets.append(
            TableAsset(
                table_id=f"p{page.page_num}_t{idx}",
                page_num=page.page_num,
                rows=row_tuple,
                markdown=_table_markdown(table_rows),
            )
        )

    enriched_page = PageContent(
        page_num=page.page_num,
        text=page.text,
        images=page.images,
        tables=page.tables,
        metadata=page.metadata,
        heading_path=page.heading_path,
        image_assets=tuple(enriched_images),
        table_assets=tuple(table_assets),
        confidence=page.confidence,
    )
    page_dict = {
        "page_num": page.page_num,
        "text": page.text,
        "tables": [asdict(t) for t in enriched_page.table_assets],
        "image_assets": [asdict(i) for i in enriched_page.image_assets],
        "heading": heading,
        "heading_path": list(page.heading_path),
        "confidence": page.confidence,
    }
    page_metrics = {
        "image_asset_count": len(enriched_page.image_assets),
        "image_caption_success_count": sum(
            1 for asset in enriched_page.image_assets if asset.caption and asset.caption.strip()
        ),
        "image_ocr_success_count": sum(
            1 for asset in enriched_page.image_assets if asset.ocr_text and asset.ocr_text.strip()
        ),
    }
    return enriched_page, page_dict, page_metrics


class DocumentFileProcessor:
    kind = "document"

    async def process(self, ctx: ProcessorContext) -> FileArtefact:
        timeout_seconds = _effective_parse_timeout_seconds(
            ctx.file_path.stat().st_size if ctx.file_path.exists() else 0
        )
        try:
            parsed_doc = await asyncio.wait_for(
                asyncio.to_thread(parse_structured, ctx.file_path, trace=ctx.trace),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Parsing {ctx.file_name} timed out after {timeout_seconds}s"
            ) from exc
        cleaned_texts, cleaning_metrics = _clean_document_page_texts_with_metrics(parsed_doc.pages)
        for page, cleaned_text in zip(parsed_doc.pages, cleaned_texts, strict=False):
            page.text = cleaned_text
        enriched_pages: list[PageContent] = []
        pages: list[dict] = []
        image_asset_count = 0
        image_caption_success_count = 0
        image_ocr_success_count = 0
        for page in parsed_doc.pages:
            enriched_page, page_dict, page_metrics = await _build_page(page, ctx)
            enriched_pages.append(enriched_page)
            pages.append(page_dict)
            image_asset_count += page_metrics["image_asset_count"]
            image_caption_success_count += page_metrics["image_caption_success_count"]
            image_ocr_success_count += page_metrics["image_ocr_success_count"]
        parsed_doc.pages = enriched_pages
        text = parsed_doc.to_text()
        metrics: dict[str, int | float | str] = {
            "page_count": parsed_doc.total_pages,
            "word_count": len(text.split()),
            "image_count": image_asset_count,
            "image_asset_count": image_asset_count,
            "image_caption_success_count": image_caption_success_count,
            "image_ocr_success_count": image_ocr_success_count,
            "table_count": sum(len(p.table_assets) for p in parsed_doc.pages),
            **cleaning_metrics,
        }
        return FileArtefact(
            text=text,
            structured_json=json.dumps(pages),
            structured_kind="pages",
            metrics=metrics,
            segments=None,
            aux_segments=None,
            parsed_doc=parsed_doc,
        )
