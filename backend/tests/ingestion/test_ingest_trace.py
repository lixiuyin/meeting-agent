"""Unit test for ingest pipeline trace coverage."""

import hashlib
import json

import pytest

from src.core.trace import TraceContext
from src.services.processor._pipeline import process_meeting_file
from src.services.rag._indexer import index_meeting


def test_process_meeting_file_trace_spans(monkeypatch, tmp_path):
    """Verify that process_meeting_file returns trace spans for each stage."""
    from src.core.database import get_write_connection

    # Create a tiny text file fixture
    text_file = tmp_path / "test_doc.txt"
    text_file.write_text(
        "This is a test document for ingest tracing with more than fifty characters."
    )

    # Create a meeting and file record
    with get_write_connection() as conn:
        from src.core.database import create_meeting, create_meeting_file

        meeting_id = create_meeting(
            conn,
            title="Trace Test",
            description="",
            meeting_date="2026-01-15",
            user_id="test",
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_name="test_doc.txt",
            file_path=str(text_file),
            file_type="txt",
        )

    trace = TraceContext()

    # Mock index_meeting to avoid vectorstore/embedder setup
    indexed = []

    def _mock_index_meeting(meeting_id, text=None, metadata=None, trace=None, **kwargs):
        indexed.append((meeting_id, text, metadata))
        if trace:
            trace.start_span("chunk", "index")
            trace.finish_span("chunk")
            trace.start_span("embed", "index")
            trace.finish_span("embed")
            trace.start_span("vectorstore_upsert", "index")
            trace.finish_span("vectorstore_upsert")

    monkeypatch.setattr("src.services.processor._pipeline.index_meeting", _mock_index_meeting)

    # Also mock index_meeting_pages (now used for txt files via parse_structured)
    monkeypatch.setattr("src.services.processor._pipeline.index_meeting_pages", _mock_index_meeting)

    # Mock processor output to avoid parser dependencies
    from src.services.parser.types import PageContent, ParsedDocument
    from src.services.processor._processors._types import FileArtefact

    mock_parsed = ParsedDocument(
        file_type="txt",
        pages=[
            PageContent(
                page_num=1,
                text="This is a test document for ingest tracing with more than fifty characters.",
            )
        ],
        metadata={},
        total_pages=1,
    )

    class _StubProcessor:
        async def process(self, ctx):
            if ctx.trace:
                ctx.trace.start_span("parse", "extract")
                ctx.trace.finish_span("parse")
            return FileArtefact(
                text="This is a test document for ingest tracing with more than fifty characters.",
                structured_json='[{"page_num":1}]',
                structured_kind="pages",
                metrics={
                    "page_count": 1,
                    "word_count": 12,
                    "cleaned_line_count": 8,
                    "removed_page_marker_count": 2,
                    "removed_repetitive_line_count": 1,
                    "image_asset_count": 3,
                    "image_caption_success_count": 2,
                    "image_ocr_success_count": 1,
                },
                parsed_doc=mock_parsed,
            )

    monkeypatch.setattr(
        "src.services.processor._pipeline._resolve_processor", lambda _ft: _StubProcessor()
    )

    import asyncio

    result_trace = asyncio.run(process_meeting_file(file_id, trace=trace))

    labels = [s.label for s in result_trace.spans]
    assert "fetch_metadata" in labels
    assert "parse" in labels or "transcribe" in labels
    assert "index_meeting" in labels
    assert "db_persist" in labels

    for span in result_trace.spans:
        assert span.duration_ms is not None, f"Span {span.label} missing duration"
        assert span.status in ("success", "error"), f"Span {span.label} non-terminal status"

    _, _, index_metadata = indexed[0]
    assert index_metadata["ingest_cleaned_line_count"] == 8
    assert index_metadata["ingest_removed_page_marker_count"] == 2
    assert index_metadata["ingest_removed_repetitive_line_count"] == 1
    assert index_metadata["ingest_image_asset_count"] == 3
    assert index_metadata["ingest_image_caption_success_count"] == 2
    assert index_metadata["ingest_image_ocr_success_count"] == 1

    with get_write_connection() as conn:
        file_row = conn.execute(
            "SELECT metrics_json FROM meeting_files WHERE id=?", (file_id,)
        ).fetchone()
    assert file_row is not None
    assert file_row["metrics_json"] is not None
    metrics_json = json.loads(file_row["metrics_json"])
    assert metrics_json["cleaned_line_count"] == 8
    assert metrics_json["image_caption_success_count"] == 2


def test_index_meeting_trace_spans():
    """Verify that index_meeting emits chunk / embed / vectorstore_upsert spans."""
    trace = TraceContext()

    # We call index_meeting with a tiny text. Because the vectorstore singleton
    # may not be initialized in this test, we mock the underlying upsert.
    from src.services.rag import _indexer as indexer_module

    original_upsert = indexer_module._upsert_with_trace
    calls = []

    def _mock_upsert(vectorstore, docs, ids, trace, meeting_id):
        calls.append((meeting_id, len(docs)))
        if trace:
            trace.start_span("embed", "index")
            trace.finish_span("embed")
            trace.start_span("vectorstore_upsert", "index")
            trace.finish_span("vectorstore_upsert")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(indexer_module, "_upsert_with_trace", _mock_upsert)

    try:
        index_meeting(
            meeting_id=999,
            text="Word " * 200,
            metadata={"title": "trace test"},
            trace=trace,
        )
    finally:
        monkeypatch.undo()

    labels = [s.label for s in trace.spans]
    assert "chunk" in labels
    assert "embed" in labels
    assert "vectorstore_upsert" in labels

    for span in trace.spans:
        assert span.duration_ms is not None
        assert span.status in ("success", "error")


def test_process_meeting_file_indexes_multimodal_when_text_short(monkeypatch, tmp_path):
    """Short extracted text should still be indexed when parsed pages contain image assets."""
    from src.core.database import get_connection, get_write_connection
    from src.services.parser.types import ImageAsset, PageContent, ParsedDocument
    from src.services.processor._processors._types import FileArtefact

    file_path = tmp_path / "deck.pptx"
    file_path.write_bytes(b"placeholder")

    with get_write_connection() as conn:
        from src.core.database import create_meeting, create_meeting_file

        meeting_id = create_meeting(
            conn,
            title="Multimodal deck",
            description="",
            meeting_date="2026-01-15",
            user_id="test",
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_name="deck.pptx",
            file_path=str(file_path),
            file_type="ppt",
        )

    parsed = ParsedDocument(
        file_type="pptx",
        pages=[
            PageContent(
                page_num=1,
                text="",
                image_assets=(
                    ImageAsset(
                        asset_id="img-1",
                        page_num=1,
                        storage_path="meeting_assets/1/1/p1_img.png",
                        caption="InstructGPT training pipeline with SFT, RM, PPO steps.",
                    ),
                ),
            )
        ],
        metadata={"parser": "local"},
        total_pages=1,
    )

    class _StubProcessor:
        async def process(self, ctx):
            return FileArtefact(
                text="short",
                structured_json='[{"page_num":1}]',
                structured_kind="pages",
                metrics={"page_count": 1, "word_count": 1},
                parsed_doc=parsed,
            )

    indexed: list[tuple[int, object]] = []

    def _mock_index_pages(meeting_id, parsed, metadata=None, trace=None):
        indexed.append((meeting_id, parsed))

    monkeypatch.setattr(
        "src.services.processor._pipeline._resolve_processor", lambda _ft: _StubProcessor()
    )
    monkeypatch.setattr("src.services.processor._pipeline.index_meeting_pages", _mock_index_pages)
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.MEETING_AUTO_SUMMARIZE_FILES", False
    )
    # Pin the chunking strategy explicitly: other tests in this file set it to
    # "text" via monkeypatch, and although monkeypatch restores after each test,
    # making the assumption here local guarantees deterministic routing under
    # ``pytest-randomly`` shuffles.
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.NON_TEXT_CHUNKING_STRATEGY", "native"
    )

    # Bypass LibreOffice-dependent pptx→pdf conversion: CI runners don't have
    # LibreOffice, and the placeholder bytes here aren't a valid pptx package
    # (python-pptx fallback would raise PackageNotFoundError). The conversion
    # path isn't under test — the multimodal-indexing branch is.
    async def _stub_convert(*, file_id, meeting_id, file_path, file_name):
        return file_path, "ppt", file_name

    monkeypatch.setattr("src.services.processor._pipeline._convert_pptx_to_pdf", _stub_convert)

    import asyncio

    asyncio.run(process_meeting_file(file_id))

    assert indexed, "Expected multimodal parsed pages to be indexed even with short text"

    with get_connection() as conn:
        file_row = conn.execute(
            "SELECT status, error_message FROM meeting_files WHERE id = ?", (file_id,)
        ).fetchone()
    assert file_row is not None
    assert file_row["status"] == "ready"
    assert file_row["error_message"] is None


def test_process_meeting_file_does_not_skip_initial_index_with_precomputed_hash(
    monkeypatch, tmp_path
):
    """New uploads with precomputed content_hash should still run initial indexing."""
    from src.core.database import get_connection, get_write_connection
    from src.services.parser.types import PageContent, ParsedDocument
    from src.services.processor._processors._types import FileArtefact

    file_path = tmp_path / "notes.txt"
    file_bytes = b"This is the first upload and it should still be indexed on initial processing."
    file_path.write_bytes(file_bytes)
    expected_hash = hashlib.sha256(file_bytes).hexdigest()

    with get_write_connection() as conn:
        from src.core.database import create_meeting, create_meeting_file

        meeting_id = create_meeting(
            conn,
            title="Hash Guard",
            description="",
            meeting_date="2026-01-15",
            user_id="test",
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_name="notes.txt",
            file_path=str(file_path),
            file_type="txt",
            content_hash=expected_hash,
        )

    parsed = ParsedDocument(
        file_type="txt",
        pages=[
            PageContent(
                page_num=1,
                text=file_bytes.decode(),
            )
        ],
        metadata={"parser": "local"},
        total_pages=1,
    )

    class _StubProcessor:
        async def process(self, ctx):
            return FileArtefact(
                text=file_bytes.decode(),
                structured_json='[{"page_num":1}]',
                structured_kind="pages",
                metrics={"page_count": 1, "word_count": 14},
                parsed_doc=parsed,
            )

    indexed: list[int] = []

    def _mock_index_pages(meeting_id, parsed, metadata=None, trace=None):
        indexed.append(meeting_id)

    monkeypatch.setattr(
        "src.services.processor._pipeline._resolve_processor", lambda _ft: _StubProcessor()
    )
    monkeypatch.setattr("src.services.processor._pipeline.index_meeting_pages", _mock_index_pages)
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.MEETING_AUTO_SUMMARIZE_FILES", False
    )

    import asyncio

    asyncio.run(process_meeting_file(file_id))

    assert indexed, "Initial processing must not be skipped when upload hash already exists"

    with get_connection() as conn:
        file_row = conn.execute(
            "SELECT status, content_hash, error_message FROM meeting_files WHERE id = ?",
            (file_id,),
        ).fetchone()
    assert file_row is not None
    assert file_row["status"] == "ready"
    assert file_row["content_hash"] == expected_hash
    assert file_row["error_message"] is None


def test_process_meeting_file_routes_audio_artefact_text_through_text_chunking(
    monkeypatch, tmp_path
):
    """When enabled, non-text files should reuse text chunking instead of segment-aware indexing."""
    from src.core.database import get_write_connection
    from src.services.processor._processors._types import FileArtefact

    audio_file = tmp_path / "call.mp3"
    audio_file.write_bytes(b"placeholder")

    with get_write_connection() as conn:
        from src.core.database import create_meeting, create_meeting_file

        meeting_id = create_meeting(
            conn,
            title="Audio route",
            description="",
            meeting_date="2026-01-15",
            user_id="test",
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_name="call.mp3",
            file_path=str(audio_file),
            file_type="audio",
        )

    long_text = (
        "Alice: project status update with enough detail to exceed the minimum ingest length.\n"
        "Bob: agreed, we should compare text chunking against segment-aware chunking."
    )
    segments = [
        {"start": 0.0, "end": 10.0, "speaker": "Alice", "text": "project status update"},
        {"start": 10.0, "end": 18.0, "speaker": "Bob", "text": "compare chunking modes"},
    ]

    class _StubProcessor:
        async def process(self, ctx):
            return FileArtefact(
                text=long_text,
                structured_json="[]",
                structured_kind="segments",
                metrics={"duration_seconds": 18.0, "word_count": len(long_text.split())},
                segments=segments,
            )

    indexed_text: list[tuple[int, str, dict | None]] = []
    indexed_segments: list[tuple[int, list[dict], dict | None]] = []

    def _mock_index_meeting(meeting_id, text=None, metadata=None, trace=None, **kwargs):
        indexed_text.append((meeting_id, text, metadata))

    def _mock_index_meeting_segments(
        meeting_id, segments=None, metadata=None, trace=None, **kwargs
    ):
        indexed_segments.append((meeting_id, segments, metadata))

    monkeypatch.setattr(
        "src.services.processor._pipeline._resolve_processor", lambda _ft: _StubProcessor()
    )
    monkeypatch.setattr("src.services.processor._pipeline.index_meeting", _mock_index_meeting)
    monkeypatch.setattr(
        "src.services.processor._pipeline.index_meeting_segments", _mock_index_meeting_segments
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.NON_TEXT_CHUNKING_STRATEGY", "text"
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.MEETING_AUTO_SUMMARIZE_FILES", False
    )

    import asyncio

    asyncio.run(process_meeting_file(file_id))

    assert len(indexed_text) == 1
    indexed_payload = indexed_text[0][1]
    assert "[00:00] Alice: project status update" in indexed_payload
    assert "[00:10] Bob: compare chunking modes" in indexed_payload
    assert indexed_text[0][2]["chunk_strategy_route"] == "text"
    assert indexed_segments == []


def test_process_meeting_file_routes_document_artefact_text_through_text_chunking(
    monkeypatch, tmp_path
):
    """When enabled, parsed documents should bypass page-aware chunking and reuse text chunking."""
    from src.core.database import get_write_connection
    from src.services.parser.types import PageContent, ParsedDocument
    from src.services.processor._processors._types import FileArtefact

    pdf_file = tmp_path / "deck.pdf"
    pdf_file.write_bytes(b"placeholder")

    with get_write_connection() as conn:
        from src.core.database import create_meeting, create_meeting_file

        meeting_id = create_meeting(
            conn,
            title="Document route",
            description="",
            meeting_date="2026-01-15",
            user_id="test",
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_name="deck.pdf",
            file_path=str(pdf_file),
            file_type="pdf",
        )

    parsed = ParsedDocument(
        file_type="pdf",
        pages=[
            PageContent(
                page_num=1,
                text=(
                    "This page contains enough text to exceed the ingest threshold and validate "
                    "that the text chunking route is selected for parsed documents."
                ),
            )
        ],
        metadata={},
        total_pages=1,
    )

    class _StubProcessor:
        async def process(self, ctx):
            return FileArtefact(
                text=parsed.to_text(),
                structured_json='[{"page_num":1}]',
                structured_kind="pages",
                metrics={"page_count": 1, "word_count": len(parsed.to_text().split())},
                parsed_doc=parsed,
            )

    indexed_text: list[tuple[int, str, dict | None]] = []
    indexed_pages: list[tuple[int, object, dict | None]] = []

    def _mock_index_meeting(meeting_id, text=None, metadata=None, trace=None, **kwargs):
        indexed_text.append((meeting_id, text, metadata))

    def _mock_index_meeting_pages(meeting_id, parsed=None, metadata=None, trace=None, **kwargs):
        indexed_pages.append((meeting_id, parsed, metadata))

    monkeypatch.setattr(
        "src.services.processor._pipeline._resolve_processor", lambda _ft: _StubProcessor()
    )
    monkeypatch.setattr("src.services.processor._pipeline.index_meeting", _mock_index_meeting)
    monkeypatch.setattr(
        "src.services.processor._pipeline.index_meeting_pages", _mock_index_meeting_pages
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.NON_TEXT_CHUNKING_STRATEGY", "text"
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.MEETING_AUTO_SUMMARIZE_FILES", False
    )

    import asyncio

    asyncio.run(process_meeting_file(file_id))

    assert len(indexed_text) == 1
    assert indexed_text[0][1] == parsed.to_text()
    assert indexed_text[0][2]["chunk_strategy_route"] == "text"
    assert indexed_pages == []
