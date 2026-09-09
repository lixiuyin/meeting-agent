"""Tests for RAG hybrid search and deletion."""

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

from src.core.database.bm25 import _build_fts_query  # noqa: E402
from src.services import rag  # noqa: E402
from src.services.rag import _rrf as rrf_module  # noqa: E402
from src.services.rag._retriever import _rrf_merge  # noqa: E402


class TestHybridSearch:
    """Test hybrid search combining BM25 and vector search."""

    def test_rrf_merge_empty_results(self):
        """Should handle empty result lists in RRF merge."""
        results = _rrf_merge([], [], 5, k=60)
        assert results == []

    def test_fts_query_preserves_hyphenated_identifier_tokens(self):
        """Query sanitization must mirror FTS5's punctuation boundaries."""
        table, query = _build_fts_query("ticket ZXQ-4817")

        assert table == "bm25_chunks"
        assert query == '"ticket" OR "ZXQ" OR "4817"'

    def test_rrf_merge_vector_only(self):
        """Should return vector results when BM25 empty."""
        vector_results = [
            {"content": "doc 1", "score": 0.9, "meeting_id": 1},
            {"content": "doc 2", "score": 0.8, "meeting_id": 2},
        ]
        results = _rrf_merge(vector_results, [], 5, k=60)
        assert len(results) == 2

    def test_rrf_merge_bm25_only(self):
        """Should return BM25 results when vector empty."""
        bm25_results = [
            {"content": "doc 1", "score": 1.5, "meeting_id": 1},
            {"content": "doc 2", "score": 1.2, "meeting_id": 2},
        ]
        results = _rrf_merge([], bm25_results, 5, k=60)
        assert len(results) == 2

    def test_rrf_merge_deduplication(self):
        """Should deduplicate same documents from both sources."""
        doc = {"content": "same doc", "meeting_id": 1}
        vector_results = [{**doc, "score": 0.9}]
        bm25_results = [{**doc, "score": 1.5}]

        results = _rrf_merge(vector_results, bm25_results, 5, k=60)
        # Should merge scores, not duplicate
        assert len(results) == 1

    def test_rrf_respects_top_k(self):
        """Should return at most top_k results."""
        vector_results = [{"content": f"doc {i}", "score": 0.9, "meeting_id": i} for i in range(10)]
        bm25_results = [
            {"content": f"doc {i + 10}", "score": 1.5, "meeting_id": i + 10} for i in range(10)
        ]

        results = _rrf_merge(vector_results, bm25_results, 5, k=60)
        assert len(results) == 5

    def test_rrf_reciprocal_rank_fusion(self):
        """RRF should combine ranks from both sources."""
        vector_results = [
            {"content": "doc A", "score": 0.9, "meeting_id": 1},  # rank 1 in vector
            {"content": "doc B", "score": 0.8, "meeting_id": 2},  # rank 2 in vector
        ]
        bm25_results = [
            {"content": "doc B", "score": 1.5, "meeting_id": 2},  # rank 1 in BM25
            {"content": "doc A", "score": 1.2, "meeting_id": 1},  # rank 2 in BM25
        ]

        results = _rrf_merge(vector_results, bm25_results, 5, k=60)
        # doc B should rank higher (1+2=3) vs doc A (1+2=3, tie but higher BM25)
        assert len(results) == 2

    def test_alpha_zero_is_pure_bm25(self, monkeypatch):
        monkeypatch.setattr(rrf_module.settings, "HYBRID_ALPHA", 0.0)
        vector = [{"content": "vector", "metadata": {"chunk_id": "v"}}]
        bm25 = [{"content": "keyword", "metadata": {"chunk_id": "b"}}]

        assert [item["content"] for item in _rrf_merge(vector, bm25, 5)] == ["keyword"]

    def test_alpha_one_is_pure_vector(self, monkeypatch):
        monkeypatch.setattr(rrf_module.settings, "HYBRID_ALPHA", 1.0)
        vector = [{"content": "vector", "metadata": {"chunk_id": "v"}}]
        bm25 = [{"content": "keyword", "metadata": {"chunk_id": "b"}}]

        assert [item["content"] for item in _rrf_merge(vector, bm25, 5)] == ["vector"]

    def test_intermediate_alpha_changes_path_preference(self, monkeypatch):
        vector = [
            {"content": "vector", "metadata": {"chunk_id": "v"}},
            {"content": "shared", "metadata": {"chunk_id": "shared"}},
        ]
        bm25 = [{"content": "keyword", "metadata": {"chunk_id": "b"}}]

        monkeypatch.setattr(rrf_module.settings, "HYBRID_ALPHA", 0.8)
        vector_heavy = _rrf_merge(vector, bm25, 5, k=60)
        monkeypatch.setattr(rrf_module.settings, "HYBRID_ALPHA", 0.2)
        keyword_heavy = _rrf_merge(vector, bm25, 5, k=60)

        assert vector_heavy[0]["content"] == "vector"
        assert keyword_heavy[0]["content"] == "keyword"
        assert vector_heavy != keyword_heavy


class TestDeleteOperations:
    """Test chunk deletion operations."""

    def test_delete_meeting_chunks(self):
        """Should delete all chunks for a meeting."""
        with patch("src.services.rag._indexer_store.get_vectorstore") as mock_get_vs:
            mock_vs = MagicMock()
            mock_get_vs.return_value = mock_vs

            rag.delete_meeting_chunks(123)

            mock_vs.delete.assert_called_once()
            call_kwargs = mock_vs.delete.call_args.kwargs
            assert call_kwargs["where"]["meeting_id"] == 123

    def test_delete_nonexistent_meeting(self):
        """Should handle deletion gracefully."""
        with patch("src.services.rag._indexer_store.get_vectorstore") as mock_get_vs:
            mock_vs = MagicMock()
            mock_get_vs.return_value = mock_vs

            # Should not raise exception
            rag.delete_meeting_chunks(99999)

            mock_vs.delete.assert_called_once()
