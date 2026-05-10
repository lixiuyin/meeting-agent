"""Tests for ingest pipeline metadata preservation (page numbers, timestamps, speakers)."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services.parser.types import ImageAsset, PageContent, ParsedDocument  # noqa: E402
from src.services.rag._indexer import (  # noqa: E402
    index_meeting_pages,
    index_meeting_segments,
)


def _make_vectorstore_mock():
    """Create a mock vectorstore that records upsert calls."""
    mock_vs = MagicMock()
    mock_vs.get.return_value = {"ids": [], "documents": []}
    mock_vs._collection = MagicMock()
    return mock_vs


class TestIndexMeetingPages:
    """Verify index_meeting_pages stamps page_number on each chunk."""

    @patch("src.services.rag._indexer.get_vectorstore")
    @patch("src.services.rag._indexer_store.get_embeddings")
    def test_page_numbers_stamped(self, mock_embed_fn, mock_get_vs):
        mock_vs = _make_vectorstore_mock()
        mock_get_vs.return_value = mock_vs
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1] * 384] * 4
        mock_embed_fn.return_value = mock_embeddings

        parsed = ParsedDocument(
            file_type="pdf",
            pages=[
                PageContent(page_num=1, text="Page one content with enough text to be useful."),
                PageContent(page_num=2, text="Page two content with enough text to be useful."),
                PageContent(
                    page_num=3,
                    text="A" * 2000,
                ),  # Large page — will be sub-split
            ],
            metadata={},
            total_pages=3,
        )

        index_meeting_pages(
            meeting_id=42,
            parsed=parsed,
            metadata={"title": "test.pdf", "file_type": "pdf"},
        )

        upsert_call = mock_vs._collection.upsert
        assert upsert_call.called
        metadatas = upsert_call.call_args[1]["metadatas"]

        # Page 1 chunk should have page_number=1
        assert metadatas[0]["page_number"] == 1
        # Page 2 chunk should have page_number=2
        assert metadatas[1]["page_number"] == 2
        # Page 3 sub-chunks should all have page_number=3
        for meta in metadatas[2:]:
            assert meta["page_number"] == 3
        # Empty heading paths should not be written to Chroma metadata.
        assert all("heading_path" not in meta for meta in metadatas)

    @patch("src.services.rag._indexer.get_vectorstore")
    @patch("src.services.rag._indexer_store.get_embeddings")
    def test_empty_parsed_skips_index(self, mock_embed_fn, mock_get_vs):
        mock_vs = _make_vectorstore_mock()
        mock_get_vs.return_value = mock_vs

        parsed = ParsedDocument(
            file_type="pdf",
            pages=[PageContent(page_num=1, text="")],
            metadata={},
            total_pages=1,
        )

        index_meeting_pages(
            meeting_id=99,
            parsed=parsed,
            metadata={"title": "empty.pdf", "file_type": "pdf"},
        )

        mock_vs._collection.upsert.assert_not_called()

    @patch("src.services.rag._indexer.get_vectorstore")
    @patch("src.services.rag._indexer_store.get_embeddings")
    def test_uncaptioned_image_skipped_when_vision_disabled(self, mock_embed_fn, mock_get_vs):
        """Uncaptioned images without OCR text should NOT be indexed (no placeholder)."""
        mock_vs = _make_vectorstore_mock()
        mock_get_vs.return_value = mock_vs
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1] * 384]
        mock_embed_fn.return_value = mock_embeddings

        parsed = ParsedDocument(
            file_type="pdf",
            pages=[
                PageContent(
                    page_num=1,
                    text="This page introduces the architecture diagram.",
                    image_assets=(
                        ImageAsset(
                            asset_id="img-1",
                            page_num=1,
                            storage_path="assets/test/image-1.png",
                            caption=None,
                            ocr_text=None,
                        ),
                    ),
                )
            ],
            metadata={},
            total_pages=1,
        )

        index_meeting_pages(
            meeting_id=1001,
            parsed=parsed,
            metadata={"title": "images.pdf", "file_type": "pdf"},
        )

        upsert_call = mock_vs._collection.upsert
        assert upsert_call.called
        documents = upsert_call.call_args[1]["documents"]
        metadatas = upsert_call.call_args[1]["metadatas"]
        image_docs = [
            (doc, md)
            for doc, md in zip(documents, metadatas, strict=False)
            if md["content_type"] == "image_asset"
        ]
        # Uncaptioned images without OCR text are skipped — no placeholder pollution
        assert len(image_docs) == 0


class TestIndexMeetingSegments:
    """Verify index_meeting_segments stamps timestamp/speaker metadata."""

    @patch("src.services.rag._indexer.get_vectorstore")
    @patch("src.services.rag._indexer.embed_documents_batched")
    def test_timestamps_and_speaker_stamped(self, mock_embed_batched, mock_get_vs):
        mock_vs = _make_vectorstore_mock()
        mock_get_vs.return_value = mock_vs
        # Two segments — with AUDIO_SPLIT_ON_SPEAKER_CHANGE=True (default), each speaker
        # gets its own chunk, so we need 2 embeddings.
        mock_embed_batched.return_value = [[0.1] * 384, [0.2] * 384]

        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello from speaker A.", "speaker": "A"},
            {"start": 5.5, "end": 10.0, "text": "Hello from speaker B.", "speaker": "B"},
        ]

        index_meeting_segments(
            meeting_id=10,
            segments=segments,
            metadata={"title": "meeting.mp3", "file_type": "audio"},
        )

        upsert_call = mock_vs._collection.upsert
        assert upsert_call.called
        metadatas = upsert_call.call_args[1]["metadatas"]

        # With AUDIO_SPLIT_ON_SPEAKER_CHANGE=True (default), different speakers land in
        # separate chunks; chunk 0 belongs to speaker A.
        assert metadatas[0]["timestamp_start"] == 0.0
        assert metadatas[0]["timestamp_end"] == 5.0
        assert metadatas[0]["speaker"] == "A"
        # Second chunk belongs to speaker B
        assert len(metadatas) >= 2
        assert metadatas[1]["speaker"] == "B"
        assert metadatas[1]["timestamp_start"] == 5.5

    @patch("src.services.rag._indexer.get_vectorstore")
    @patch("src.services.rag._indexer.embed_documents_batched")
    def test_large_segments_grouped_by_size(self, mock_embed_batched, mock_get_vs):
        mock_vs = _make_vectorstore_mock()
        mock_get_vs.return_value = mock_vs
        mock_embed_batched.return_value = [[0.1] * 384, [0.2] * 384]

        # Create enough text to force chunking
        long_text = "Word " * 400  # ~2000 chars
        segments = [
            {"start": 0.0, "end": 60.0, "text": long_text, "speaker": "A"},
            {"start": 60.5, "end": 120.0, "text": "Short ending.", "speaker": "B"},
        ]

        index_meeting_segments(
            meeting_id=11,
            segments=segments,
            metadata={"title": "long.wav", "file_type": "audio"},
        )

        upsert_call = mock_vs._collection.upsert
        assert upsert_call.called
        # Each speaker gets its own chunk due to speaker-change splitting
        docs = upsert_call.call_args[1]["documents"]
        assert len(docs) >= 2

    @patch("src.services.rag._indexer.get_vectorstore")
    def test_empty_segments_skips_index(self, mock_get_vs):
        mock_vs = _make_vectorstore_mock()
        mock_get_vs.return_value = mock_vs

        index_meeting_segments(
            meeting_id=12,
            segments=[],
            metadata={"title": "empty.wav", "file_type": "audio"},
        )

        mock_vs._collection.upsert.assert_not_called()


class TestFormatDocsMetadata:
    """Verify _format_docs and _extract_sources preserve all metadata fields."""

    def test_format_docs_with_timestamps_and_speaker(self):
        from src.services.chain._formatting import _format_docs

        docs = [
            {
                "content": "Hello world",
                "metadata": {
                    "meeting_id": 1,
                    "title": "Team Standup",
                    "file_name": "standup.mp3",
                    "timestamp_start": 10.5,
                    "timestamp_end": 25.3,
                    "speaker": "Alice",
                },
            }
        ]
        result = _format_docs(docs)
        # Format is: [N] Meet#<id> · <file> · <ts>s-<ts>s (<speaker>)\n<content>
        assert "Meet#1" in result
        assert "standup.mp3" in result
        assert "10s-25s" in result
        assert "(Alice)" in result

    def test_format_docs_with_page_number(self):
        from src.services.chain._formatting import _format_docs

        docs = [
            {
                "content": "Slide content",
                "metadata": {
                    "meeting_id": 2,
                    "title": "Q4 Review",
                    "file_name": "review.pptx",
                    "page_number": 5,
                },
            }
        ]
        result = _format_docs(docs)
        # Format is: [N] Meet#<id> · <file> · p<page>\n<content>
        assert "Meet#2" in result
        assert "p5" in result

    def test_extract_sources_deduplication(self):
        from src.services.chain._formatting import _extract_sources

        docs = [
            {
                "content": "Chunk A",
                "score": 0.9,
                "metadata": {
                    "meeting_id": 1,
                    "file_id": 10,
                    "chunk_index": 0,
                    "title": "M1",
                },
            },
            {
                "content": "Chunk A (duplicate)",
                "score": 0.85,
                "metadata": {
                    "meeting_id": 1,
                    "file_id": 10,
                    "chunk_index": 0,
                    "title": "M1",
                },
            },
            {
                "content": "Chunk B",
                "score": 0.8,
                "metadata": {
                    "meeting_id": 1,
                    "file_id": 10,
                    "chunk_index": 1,
                    "title": "M1",
                },
            },
        ]
        sources = _extract_sources(docs)
        # Duplicate chunk (same meeting:file:chunk key) should be deduped
        assert len(sources) == 2
        assert sources[0]["chunk_index"] == 0
        assert sources[1]["chunk_index"] == 1

    def test_extract_sources_preserves_metadata_fields(self):
        from src.services.chain._formatting import _extract_sources

        docs = [
            {
                "content": "Audio chunk",
                "score": 0.95,
                "metadata": {
                    "meeting_id": 5,
                    "file_id": 20,
                    "chunk_index": 3,
                    "title": "All Hands",
                    "file_name": "allhands.mp4",
                    "page_number": None,
                    "timestamp_start": 120.0,
                    "timestamp_end": 180.5,
                    "speaker": "Bob",
                    "page_image_storage_path": "meeting_assets/5/20/p3_slide.png",
                    "page_image_thumbnail_path": "meeting_assets/5/20/p3_slide.thumb.webp",
                },
            }
        ]
        sources = _extract_sources(docs)
        assert len(sources) == 1
        s = sources[0]
        assert s["meeting_id"] == 5
        assert s["file_id"] == 20
        assert s["chunk_index"] == 3
        assert s["file_name"] == "allhands.mp4"
        assert s["timestamp_start"] == 120.0
        assert s["timestamp_end"] == 180.5
        assert s["speaker"] == "Bob"
        assert s["page_number"] is None
        assert s["page_image_path"] == "meeting_assets/5/20/p3_slide.png"
        assert s["page_image_thumbnail_path"] == "meeting_assets/5/20/p3_slide.thumb.webp"
