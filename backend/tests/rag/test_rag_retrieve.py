"""Tests for RAG document retrieval operations."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services import rag  # noqa: E402
from src.services.rag._raganything import _ensure_operation_succeeded  # noqa: E402
from src.services.rag._retriever import (  # noqa: E402
    _bm25_retrieve,
    _vector_retrieve,
    retrieve,
    retrieve_sibling_chunks,
)


class TestVectorRetrieve:
    """Test vector retrieval operations."""

    def test_vector_retrieve_empty_results(self):
        """Should handle empty retrieval results gracefully."""
        with patch("src.services.rag._retriever.get_vectorstore") as mock_get_vs:
            mock_vs = MagicMock()
            mock_vs.similarity_search_with_score.return_value = []
            mock_get_vs.return_value = mock_vs

            results = _vector_retrieve("test query", {}, 5)
            assert results == []

    def test_vector_retrieve_with_meeting_filter(self):
        """Should filter by meeting IDs when provided."""
        with patch("src.services.rag._retriever.get_vectorstore") as mock_get_vs:
            mock_vs = MagicMock()
            mock_doc = MagicMock()
            mock_doc.metadata = {"meeting_id": "123"}
            mock_doc.page_content = "test content"
            mock_vs.similarity_search_with_score.return_value = [(mock_doc, 0.5)]
            mock_get_vs.return_value = mock_vs

            _ = _vector_retrieve("test", {"meeting_id": {"$in": [123]}}, 5)

            mock_vs.similarity_search_with_score.assert_called_once()
            call_kwargs = mock_vs.similarity_search_with_score.call_args.kwargs
            assert call_kwargs["filter"]["meeting_id"]["$in"] == [123]

    def test_vector_retrieve_formats_results(self):
        """Should format retrieval results correctly."""
        with patch("src.services.rag._retriever.get_vectorstore") as mock_get_vs:
            mock_vs = MagicMock()
            mock_doc = MagicMock()
            mock_doc.metadata = {"meeting_id": 1, "title": "Test Meeting"}
            mock_doc.page_content = "test content"
            mock_vs.similarity_search_with_score.return_value = [(mock_doc, 0.75)]
            mock_get_vs.return_value = mock_vs

            results = _vector_retrieve("test", {}, 5)

            assert len(results) == 1
            assert results[0]["content"] == "test content"
            assert results[0]["score"] == 0.75
            assert results[0]["score_kind"] == "distance"
            assert results[0]["metadata"]["meeting_id"] == 1

    def test_public_retrieve_normalizes_vector_distance(self, monkeypatch):
        """Downstream consumers always receive higher-is-better relevance."""
        monkeypatch.setattr("src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "vector")
        monkeypatch.setattr("src.services.rag._retriever.settings.DISTANCE_METRIC", "l2")
        monkeypatch.setattr("src.services.rag._retriever.settings.TOP_K", 5)
        with patch("src.services.rag._retriever.get_vectorstore") as mock_get_vs:
            mock_vs = MagicMock()
            mock_doc = MagicMock()
            mock_doc.metadata = {"meeting_id": 1}
            mock_doc.page_content = "best"
            mock_vs.similarity_search_with_score.return_value = [(mock_doc, 0.25)]
            mock_get_vs.return_value = mock_vs

            results, _ = retrieve("test")

        assert results[0]["score"] == pytest.approx(0.8)
        assert results[0]["score_kind"] == "relevance"

    def test_vector_retrieve_cosine_uses_max_distance_threshold(self, monkeypatch):
        """Cosine distance should keep lower scores and drop higher scores."""
        monkeypatch.setattr("src.services.rag._retriever.settings.DISTANCE_METRIC", "cosine")
        with patch("src.services.rag._retriever.get_vectorstore") as mock_get_vs:
            mock_vs = MagicMock()
            good_doc = MagicMock()
            good_doc.metadata = {"meeting_id": 1}
            good_doc.page_content = "good"
            bad_doc = MagicMock()
            bad_doc.metadata = {"meeting_id": 1}
            bad_doc.page_content = "bad"
            mock_vs.similarity_search_with_score.return_value = [(good_doc, 0.3), (bad_doc, 0.9)]
            mock_get_vs.return_value = mock_vs

            results = _vector_retrieve("test", {}, 5, threshold=0.5)
            assert [r["content"] for r in results] == ["good"]


class TestBM25Operations:
    """Test BM25 full-text search operations."""

    def test_bm25_retrieve_no_index(self):
        """Should return empty list when BM25 index not loaded."""
        with patch("src.core.database.fts5_search", side_effect=Exception("no such table")):
            results = _bm25_retrieve("query", None, None, 5)
            assert results == []

    def test_rebuild_bm25_from_chroma_empty(self):
        """Should handle empty Chroma collection gracefully."""
        with patch("src.services.rag._retriever.get_vectorstore") as mock_get_vs:
            mock_vs = MagicMock()
            mock_vs.get.return_value = {"ids": [], "documents": [], "metadatas": []}
            mock_get_vs.return_value = mock_vs

            # Should not raise exception
            rag.rebuild_bm25_from_chroma()


class TestRAGAnythingToggle:
    """Test optional RAGAnything retrieval branch behavior."""

    def test_raganything_fallback_to_native(self, monkeypatch):
        monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
        monkeypatch.setattr(
            "src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "multimodal"
        )
        monkeypatch.setattr(
            "src.services.rag._retriever.settings.RAGANYTHING_FALLBACK_TO_NATIVE", True
        )
        monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", False)
        monkeypatch.setattr("src.services.rag._retriever.settings.TOP_K", 5)

        with (
            patch(
                "src.services.rag._retriever.retrieve_with_raganything",
                side_effect=RuntimeError("raganything unavailable"),
            ),
            patch(
                "src.services.rag._retriever._vector_retrieve",
                return_value=[{"content": "native", "metadata": {}, "score": 0.1}],
            ) as mock_vector,
        ):
            out, _qa = retrieve("test")
            assert len(out) == 1
            assert out[0]["content"] == "native"
            mock_vector.assert_called_once()

    def test_raganything_no_fallback_raises(self, monkeypatch):
        monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
        monkeypatch.setattr(
            "src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "multimodal"
        )
        monkeypatch.setattr(
            "src.services.rag._retriever.settings.RAGANYTHING_FALLBACK_TO_NATIVE", False
        )
        monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", False)

        with patch(
            "src.services.rag._retriever.retrieve_with_raganything",
            side_effect=RuntimeError("raganything unavailable"),
        ):
            with pytest.raises(RuntimeError, match="raganything unavailable"):
                _ = retrieve("test")

    def test_hybrid_multimodal_falls_back_to_vector_on_raganything_error(self, monkeypatch):
        monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
        monkeypatch.setattr(
            "src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "hybrid_multimodal"
        )
        monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", False)
        monkeypatch.setattr("src.services.rag._retriever.settings.TOP_K", 5)

        with (
            patch(
                "src.services.rag._retriever._vector_retrieve",
                return_value=[
                    {"content": "vector-1", "metadata": {"meeting_id": 1}, "score": 0.2},
                    {"content": "vector-2", "metadata": {"meeting_id": 1}, "score": 0.1},
                ],
            ),
            patch(
                "src.services.rag._retriever.retrieve_with_raganything",
                side_effect=TimeoutError("raganything timeout"),
            ),
        ):
            out, _qa = retrieve("test")
            assert [d["content"] for d in out] == ["vector-1", "vector-2"]


class TestScopedQueryShortCircuit:
    """Verify that scoped queries (file_ids/meeting_ids) skip RAGAnything."""

    def test_file_ids_skips_raganything_hybrid_multimodal(self, monkeypatch):
        monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
        monkeypatch.setattr(
            "src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "hybrid_multimodal"
        )
        monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", False)
        monkeypatch.setattr("src.services.rag._retriever.settings.TOP_K", 5)

        with (
            patch(
                "src.services.rag._retriever._vector_retrieve",
                return_value=[{"content": "v1", "metadata": {}, "score": 0.1}],
            ) as mock_vector,
            patch(
                "src.services.rag._retriever.retrieve_with_raganything",
            ) as mock_ra,
        ):
            out, _qa = retrieve("test", file_ids=[6])
            assert len(out) == 1
            mock_vector.assert_called_once()
            mock_ra.assert_not_called()

    def test_meeting_ids_skips_raganything_raganything_provider(self, monkeypatch):
        monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
        monkeypatch.setattr(
            "src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "multimodal"
        )
        monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", True)
        monkeypatch.setattr("src.services.rag._retriever.settings.TOP_K", 5)

        with (
            patch(
                "src.services.rag._retriever._vector_retrieve",
                return_value=[{"content": "v1", "metadata": {}, "score": 0.1}],
            ) as mock_vector,
            patch(
                "src.services.rag._retriever.retrieve_with_raganything",
            ) as mock_ra,
        ):
            out, _qa = retrieve("test", meeting_ids=[1])
            assert len(out) == 1
            mock_vector.assert_called_once()
            mock_ra.assert_not_called()

    def test_no_scope_uses_raganything_normally(self, monkeypatch):
        monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
        monkeypatch.setattr(
            "src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "hybrid_multimodal"
        )
        monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", False)
        monkeypatch.setattr("src.services.rag._retriever.settings.TOP_K", 5)

        with (
            patch(
                "src.services.rag._retriever._vector_retrieve",
                return_value=[{"content": "v1", "metadata": {}, "score": 0.1}],
            ),
            patch(
                "src.services.rag._retriever.retrieve_with_raganything",
                return_value=[{"content": "ra1", "metadata": {}, "score": 0.2}],
            ) as mock_ra,
        ):
            out, _qa = retrieve("test")
            mock_ra.assert_called_once()


class TestSiblingCoRetrieve:
    def test_retrieve_sibling_chunks_dedups_and_limits(self):
        docs = [
            {
                "content": "anchor",
                "metadata": {"meeting_id": 1, "file_id": 10, "page_number": 2, "chunk_index": 1},
                "score": 0.2,
            }
        ]
        sibling_rows = [
            {
                "content": "table chunk",
                "metadata": {
                    "meeting_id": 1,
                    "file_id": 10,
                    "page_number": 2,
                    "chunk_index": 3,
                    "content_type": "table",
                },
                "score": 0.0,
            },
            {
                "content": "duplicate anchor",
                "metadata": {
                    "meeting_id": 1,
                    "file_id": 10,
                    "page_number": 2,
                    "chunk_index": 1,
                    "content_type": "image_caption",
                },
                "score": 0.0,
            },
        ]
        with (
            patch("src.services.rag._retriever.get_connection") as mock_conn,
            patch("src.services.rag._retriever.get_page_sibling_chunks", return_value=sibling_rows),
        ):
            mock_conn.return_value.__enter__.return_value = MagicMock()
            out = retrieve_sibling_chunks(docs, max_per_anchor=2, max_total=1)
            assert len(out) == 1
            assert out[0]["content"] == "table chunk"


class TestRAGAnythingResultValidation:
    def test_rejects_explicit_failure_payload(self):
        with pytest.raises(RuntimeError, match="index failed: worker timeout"):
            _ensure_operation_succeeded(
                {"success": False, "error": "worker timeout"},
                operation="index",
            )

    def test_rejects_non_success_status_payload(self):
        with pytest.raises(RuntimeError, match="query failed: extraction failed"):
            _ensure_operation_succeeded(
                {"status": "error", "message": "extraction failed"},
                operation="query",
            )

    def test_allows_success_payload(self):
        payload = {"status": "success", "data": {"chunks": []}}
        out = _ensure_operation_succeeded(payload, operation="query")
        assert out == payload
