"""Tests for RAG chunk ID collision fix and multi-file coexistence."""

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

from src.services.parser.types import PageContent, ParsedDocument  # noqa: E402
from src.services.rag._indexer import (  # noqa: E402
    _SEPARATORS,
    _chunk_id_prefix,
    _index_flat,
    _index_parent_child,
    index_meeting_pages,
)


class TestChunkIdPrefix:
    """Test the _chunk_id_prefix helper."""

    def test_prefix_without_file_id(self):
        assert _chunk_id_prefix(1, None) == "meeting_1"

    def test_prefix_with_file_id(self):
        assert _chunk_id_prefix(1, 6) == "meeting_1_file_6"

    def test_prefix_with_zero_file_id(self):
        assert _chunk_id_prefix(5, 0) == "meeting_5_file_0"

    def test_prefix_separates_auxiliary_source_kind(self):
        assert _chunk_id_prefix(5, 7, "image") == "meeting_5_file_7_source_image"
        assert _chunk_id_prefix(5, 7) != _chunk_id_prefix(5, 7, "image")

    def test_prefix_separates_shadow_generation(self):
        assert (
            _chunk_id_prefix(5, 7, "image", "A1-B2")
            == "meeting_5_file_7_source_image_generation_a1_b2"
        )

    def test_index_generation_produces_shadow_ids(self):
        mock_vs = MagicMock()
        captured_ids: list[str] = []

        def capture_upsert(ids, **kwargs):
            captured_ids.extend(ids)

        mock_vs._collection.upsert = capture_upsert
        with (
            patch("src.services.rag._indexer.get_vectorstore", return_value=mock_vs),
            patch("src.services.rag._indexer_store.get_embeddings") as mock_embed,
            patch("src.services.rag._indexer.settings") as mock_settings,
        ):
            mock_settings.CHUNK_SIZE = 500
            mock_settings.CHUNK_OVERLAP = 50
            mock_settings.SEMANTIC_CHUNKING_ENABLED = False
            mock_embed.return_value.embed_documents.return_value = [[0.1] * 10]
            _index_flat(
                1,
                "Hello world",
                {"file_id": 42, "index_generation": "new-generation"},
                _SEPARATORS,
            )

        assert captured_ids == ["meeting_1_file_42_generation_new_generation_chunk_0"]


class TestIndexMeetingPagesChunkIds:
    """Verify index_meeting_pages generates unique IDs per file."""

    def test_two_files_same_meeting_unique_ids(self):
        """Two files under the same meeting must produce non-overlapping IDs."""
        mock_vs = MagicMock()
        mock_vs.get.return_value = {"ids": [], "documents": []}
        indexed_ids: list[list[str]] = []

        def capture_upsert(ids, **kwargs):
            indexed_ids.append(list(ids))

        mock_vs._collection.upsert = capture_upsert

        parsed = ParsedDocument(
            file_type="pdf",
            pages=[PageContent(page_num=1, text="Hello world from file A")],
            metadata={},
            total_pages=1,
        )

        with (
            patch("src.services.rag._indexer.get_vectorstore", return_value=mock_vs),
            patch("src.services.rag._indexer_store.get_embeddings") as mock_embed,
            patch("src.services.rag._indexer.delete_meeting_chunks"),
            patch("src.services.rag._indexer.settings") as mock_settings,
        ):
            mock_settings.CHUNK_SIZE = 500
            mock_settings.CHUNK_OVERLAP = 50
            mock_settings.RAG_INDEX_TABLES = False
            mock_settings.RAG_INDEX_IMAGE_CAPTIONS = False
            mock_embed.return_value.embed_documents.return_value = [[0.1] * 10]

            # Index file 5
            index_meeting_pages(1, parsed, {"file_id": 5, "file_type": "pdf", "title": "A"})
            # Index file 6
            index_meeting_pages(1, parsed, {"file_id": 6, "file_type": "pdf", "title": "B"})

        assert len(indexed_ids) == 2
        ids_file5 = set(indexed_ids[0])
        ids_file6 = set(indexed_ids[1])
        assert ids_file5.isdisjoint(ids_file6), (
            f"IDs should not overlap but got: {ids_file5 & ids_file6}"
        )
        assert all("file_5" in id_ for id_ in ids_file5)
        assert all("file_6" in id_ for id_ in ids_file6)


class TestIndexFlatChunkIds:
    """Verify _index_flat generates file-aware IDs."""

    def test_flat_ids_include_file_id(self):
        mock_vs = MagicMock()
        mock_vs.get.return_value = {"ids": [], "documents": []}
        captured_ids: list[str] = []

        def capture_upsert(ids, **kwargs):
            captured_ids.extend(ids)

        mock_vs._collection.upsert = capture_upsert

        with (
            patch("src.services.rag._indexer.get_vectorstore", return_value=mock_vs),
            patch("src.services.rag._indexer_store.get_embeddings") as mock_embed,
            patch("src.services.rag._indexer.settings") as mock_settings,
        ):
            mock_settings.CHUNK_SIZE = 500
            mock_settings.CHUNK_OVERLAP = 50
            mock_settings.SEMANTIC_CHUNKING_ENABLED = False
            mock_embed.return_value.embed_documents.return_value = [[0.1] * 10]

            _index_flat(1, "Hello world", {"file_id": 42, "file_type": "txt"}, _SEPARATORS)

        assert len(captured_ids) == 1
        assert captured_ids[0] == "meeting_1_file_42_chunk_0"


class TestIndexParentChildChunkIds:
    """Verify _index_parent_child generates file-aware IDs."""

    def test_parent_child_ids_include_file_id(self):
        mock_vs = MagicMock()
        mock_vs.get.return_value = {"ids": [], "documents": []}
        captured_ids: list[str] = []

        def capture_upsert(ids, **kwargs):
            captured_ids.extend(ids)

        mock_vs._collection.upsert = capture_upsert

        with (
            patch("src.services.rag._indexer.get_vectorstore", return_value=mock_vs),
            patch("src.services.rag._indexer_store.get_embeddings") as mock_embed,
            patch("src.services.rag._indexer.settings") as mock_settings,
        ):
            mock_settings.CHUNK_SIZE = 100
            mock_settings.CHUNK_OVERLAP = 10
            mock_settings.CHILD_CHUNK_SIZE = 20
            mock_settings.CHILD_CHUNK_OVERLAP = 5
            mock_settings.PARENT_CHILD_ENABLED = True
            mock_embed.return_value.embed_documents.return_value = [[0.1] * 10]

            text = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda."
            _index_parent_child(2, text, {"file_id": 7, "file_type": "pdf"}, _SEPARATORS)

        # All IDs must contain file_7
        for cid in captured_ids:
            assert "file_7" in cid, f"Expected file_7 in ID: {cid}"

        # Must have at least one parent and one child
        parents = [c for c in captured_ids if "_parent_" in c]
        children = [c for c in captured_ids if "_child_" in c]
        assert len(parents) >= 1
        assert len(children) >= 1
