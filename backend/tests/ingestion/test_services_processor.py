"""Tests for processor optional multimodal extensions."""

from pathlib import Path

import pytest

from src.services.parser.types import ImageAsset, PageContent, ParsedDocument
from src.services.processor._processors._types import ProcessorContext
from src.services.processor._processors.av import _build_keyframe_segments
from src.services.processor._processors.document import (
    _build_page_dict,
    _clean_document_page_texts,
    _clean_document_page_texts_with_metrics,
    _enrich_image_asset,
)
from src.services.processor._processors.image import ImageFileProcessor


@pytest.mark.asyncio
async def test_video_keyframe_segments_generated_when_enabled(monkeypatch):
    from src.services.processor._processors import av as av_module

    monkeypatch.setattr(av_module.settings, "VIDEO_KEYFRAMES_ENABLED", True)
    monkeypatch.setattr(av_module.settings, "MULTIMODAL_CAPTIONING_ENABLED", False)
    ctx = ProcessorContext(
        file_id=1,
        meeting_id=1,
        file_type="video",
        file_name="demo.mp4",
        file_path=Path("/tmp/demo.mp4"),
        meeting_date=None,
        trace=None,
    )
    segments = await _build_keyframe_segments(ctx, duration_seconds=95.0)
    assert segments is not None
    assert len(segments) == 3
    assert segments[0]["start"] == 30.0
    assert "Keyframe at" in segments[0]["text"]


@pytest.mark.asyncio
async def test_document_page_includes_image_captions_when_enabled(monkeypatch):
    from src.services.processor._processors import document as doc_module

    monkeypatch.setattr(doc_module.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)

    async def _caption(_path: str) -> str | None:
        return "A whiteboard with action items"

    monkeypatch.setattr(doc_module, "caption_image", _caption)
    page = PageContent(
        page_num=1,
        text="Sprint planning notes",
        images=[{"path": "/tmp/page-1-img.png"}],
        tables=None,
    )
    payload = await _build_page_dict(page)
    assert payload["image_captions"] == [
        {
            "image_ref": {"path": "/tmp/page-1-img.png"},
            "caption": "A whiteboard with action items",
        }
    ]


@pytest.mark.asyncio
async def test_image_semantics_fallback_used_when_caption_empty(monkeypatch):
    from src.services.processor._processors import document as doc_module

    monkeypatch.setattr(doc_module.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)
    monkeypatch.setattr(doc_module.settings, "RAG_IMAGE_OCR_MIN_LENGTH", 15)

    async def _caption(_path: str) -> str | None:
        return None

    async def _semantic(_path: str) -> str | None:
        return "A flowchart with three connected decision nodes."

    async def _ocr(_path: str) -> str | None:
        return None

    monkeypatch.setattr(doc_module, "caption_image", _caption)
    monkeypatch.setattr(doc_module, "describe_image_semantics", _semantic)
    monkeypatch.setattr(doc_module, "transcribe_text_bearing_image", _ocr)

    enriched = await _enrich_image_asset(
        ImageAsset(asset_id="img-1", page_num=1, storage_path="assets/m1/f1/p1.png")
    )
    assert enriched.caption == "A flowchart with three connected decision nodes."


@pytest.mark.asyncio
async def test_image_semantics_fallback_used_when_caption_is_low_information(monkeypatch):
    from src.services.processor._processors import document as doc_module

    monkeypatch.setattr(doc_module.settings, "MULTIMODAL_CAPTIONING_ENABLED", True)
    monkeypatch.setattr(doc_module.settings, "RAG_IMAGE_OCR_MIN_LENGTH", 15)

    async def _caption(_path: str) -> str | None:
        return "N/A"

    async def _semantic(_path: str) -> str | None:
        return "A dashboard chart comparing quarterly revenue and retention."

    async def _ocr(_path: str) -> str | None:
        return "..."

    monkeypatch.setattr(doc_module, "caption_image", _caption)
    monkeypatch.setattr(doc_module, "describe_image_semantics", _semantic)
    monkeypatch.setattr(doc_module, "transcribe_text_bearing_image", _ocr)

    enriched = await _enrich_image_asset(
        ImageAsset(asset_id="img-2", page_num=1, storage_path="assets/m1/f1/p2.png")
    )
    assert enriched.caption == "A dashboard chart comparing quarterly revenue and retention."
    assert enriched.ocr_text is None
    assert enriched.is_text_bearing is False


@pytest.mark.asyncio
async def test_image_processor_indexes_caption_when_ocr_empty(monkeypatch):
    from src.services.processor._processors import image as image_module

    async def _caption(_path: str) -> str | None:
        return "A product roadmap board with Q1 to Q4 milestones."

    def _parse(_path: Path, trace=None) -> ParsedDocument:
        return ParsedDocument(
            file_type="image",
            pages=[PageContent(page_num=1, text="  ")],
            metadata={},
            total_pages=1,
        )

    monkeypatch.setattr(image_module, "caption_image", _caption)
    monkeypatch.setattr(image_module, "parse_structured", _parse)

    processor = ImageFileProcessor()
    artefact = await processor.process(
        ProcessorContext(
            file_id=1,
            meeting_id=1,
            file_type="image",
            file_name="board.png",
            file_path=Path("uploads/board.png"),
            meeting_date=None,
            trace=None,
        )
    )

    assert artefact.text == "A product roadmap board with Q1 to Q4 milestones."
    assert artefact.segments is not None
    assert artefact.segments[0]["text"] == artefact.text
    assert '"ocr_text": null' in (artefact.structured_json or "")


@pytest.mark.asyncio
async def test_document_processor_emits_cleaning_and_image_quality_metrics(monkeypatch, tmp_path):
    from src.services.processor._processors import document as doc_module

    monkeypatch.setattr(doc_module.settings, "MULTIMODAL_CAPTIONING_ENABLED", False)

    parsed = ParsedDocument(
        file_type="pdf",
        pages=[
            PageContent(
                page_num=1,
                text="Page 1 of 3\nAgenda\nCompany Confidential",
                image_assets=(
                    ImageAsset(
                        asset_id="img-1",
                        page_num=1,
                        storage_path="assets/m1/f1/p1.png",
                        caption="Roadmap overview",
                        ocr_text="Q1 milestones",
                    ),
                ),
            ),
            PageContent(
                page_num=2,
                text="Page 2 of 3\nSummary\nCompany Confidential",
            ),
            PageContent(
                page_num=3,
                text="Page 3 of 3\nDecisions\nCompany Confidential",
            ),
        ],
        metadata={},
        total_pages=3,
    )

    def _parse_structured(_path: Path, trace=None) -> ParsedDocument:
        return parsed

    monkeypatch.setattr(doc_module, "parse_structured", _parse_structured)
    file_path = tmp_path / "deck.pdf"
    file_path.write_bytes(b"pdf")

    artefact = await doc_module.DocumentFileProcessor().process(
        ProcessorContext(
            file_id=1,
            meeting_id=1,
            file_type="pdf",
            file_name="deck.pdf",
            file_path=file_path,
            meeting_date=None,
            trace=None,
        )
    )

    assert artefact.metrics["cleaned_line_count"] == 3
    assert artefact.metrics["removed_page_marker_count"] == 3
    assert artefact.metrics["removed_repetitive_line_count"] == 3
    assert artefact.metrics["image_asset_count"] == 1
    assert artefact.metrics["image_caption_success_count"] == 1
    assert artefact.metrics["image_ocr_success_count"] == 1


def test_clean_document_page_texts_removes_page_markers_and_repeated_footer():
    pages = [
        PageContent(
            page_num=1,
            text="Company Confidential - Page 1 of 3\nQuarterly review\nCompany Confidential - 1/3",
        ),
        PageContent(
            page_num=2,
            text="Company Confidential - Page 2 of 3\nRoadmap highlights\nCompany Confidential - 2/3",
        ),
        PageContent(
            page_num=3,
            text="Company Confidential - Page 3 of 3\nRisks and mitigations\nCompany Confidential - 3/3",
        ),
    ]

    cleaned = _clean_document_page_texts(pages)
    assert cleaned[0] == "Quarterly review"
    assert cleaned[1] == "Roadmap highlights"
    assert cleaned[2] == "Risks and mitigations"


def test_clean_document_page_texts_keeps_meaningful_short_and_numeric_content():
    pages = [
        PageContent(
            page_num=1,
            text="Project timeline:\n1\nDiscovery completed\n2\nPilot launch in 2024\n1",
        ),
        PageContent(
            page_num=2,
            text="Financial highlights:\n2023\nRevenue reached 1.2M in 2024\n2",
        ),
    ]

    cleaned = _clean_document_page_texts(pages)
    assert cleaned[0].splitlines() == [
        "Project timeline:",
        "1",
        "Discovery completed",
        "2",
        "Pilot launch in 2024",
    ]
    assert cleaned[1].splitlines() == [
        "Financial highlights:",
        "2023",
        "Revenue reached 1.2M in 2024",
    ]


def test_clean_document_page_texts_with_metrics_counts_quality_signals():
    pages = [
        PageContent(page_num=1, text="Title\n1\nCompany Confidential"),
        PageContent(page_num=2, text="Agenda\nPage 2 of 3\nCompany Confidential"),
        PageContent(page_num=3, text="Summary\n3/3\nCompany Confidential"),
    ]

    cleaned, metrics = _clean_document_page_texts_with_metrics(pages)
    assert cleaned == ["Title", "Agenda", "Summary"]
    assert metrics["cleaned_line_count"] == 3
    assert metrics["removed_page_marker_count"] == 3
    assert metrics["removed_repetitive_line_count"] == 3
