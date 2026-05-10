"""Tests for RAG document reranking functionality."""

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

from src.core.config import settings  # noqa: E402
from src.services import rag  # noqa: E402


class TestRerankOperations:
    """Test document reranking functionality."""

    def test_rerank_empty_docs(self):
        """Should handle empty document list."""
        results = rag.rerank("query", [], 5)
        assert results == []

    def test_rerank_single_doc(self):
        """Should handle single document without reranking."""
        docs = [{"content": "only doc", "score": 0.5}]
        with patch.object(settings, "RERANKER_BINDING", ""):
            results = rag.rerank("query", docs, 5)
        assert results == [{**d, "reranked": False} for d in docs]

    def test_rerank_with_cohere_binding(self):
        """Should use Cohere when configured."""
        docs = [
            {"content": "doc 1", "score": 0.8},
            {"content": "doc 2", "score": 0.6},
        ]

        # Directly set settings values
        orig_binding = settings.RERANKER_BINDING
        orig_key = getattr(settings, "COHERE_API_KEY", None)
        settings.RERANKER_BINDING = "cohere"
        # COHERE_API_KEY not a direct setting field

        with patch("src.services.rag._reranker._rerank_cohere") as mock_cohere:
            mock_cohere.return_value = [docs[1], docs[0]]  # Reversed

            results = rag.rerank("query", docs, 2)

            mock_cohere.assert_called_once()
            assert results == [docs[1], docs[0]]

        # Restore settings
        settings.RERANKER_BINDING = orig_binding
        if orig_key is not None:
            settings.COHERE_API_KEY = orig_key

    def test_rerank_with_cohere_binding_and_base_url(self):
        """Should use HTTP path when base_url is configured (e.g., OpenRouter)."""
        docs = [
            {"content": "doc 1", "score": 0.8},
            {"content": "doc 2", "score": 0.6},
        ]

        with (
            patch.object(settings, "RERANKER_BINDING", "cohere"),
            patch.object(settings, "RERANKER_BASE_URL", "https://openrouter.ai/api/v1"),
            patch("httpx.post") as mock_post,
        ):
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "results": [
                    {"index": 1, "relevance_score": 0.99},
                    {"index": 0, "relevance_score": 0.88},
                ]
            }
            mock_post.return_value = mock_response

            results = rag.rerank("query", docs, 2)

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args.kwargs
            assert call_kwargs["json"]["model"] == "cohere/rerank-4-pro"
            assert call_kwargs["json"]["query"] == "query"
            assert call_kwargs["json"]["top_n"] == 2
            assert results[0]["content"] == "doc 2"
            assert results[0]["score"] == 0.99

    def test_rerank_cohere_http_failure_returns_original(self):
        """Should return original docs with reranked=False when HTTP rerank fails."""
        docs = [{"content": "doc 1", "score": 0.8}]

        with (
            patch.object(settings, "RERANKER_BINDING", "cohere"),
            patch.object(settings, "RERANKER_BASE_URL", "https://openrouter.ai/api/v1"),
            patch("httpx.post", side_effect=Exception("timeout")),
        ):
            results = rag.rerank("query", docs, 2)
            assert results == [{**d, "reranked": False} for d in docs]

    def test_rerank_with_bge_binding(self):
        """Should use BGE when configured."""
        docs = [
            {"content": "doc 1", "score": 0.8},
            {"content": "doc 2", "score": 0.6},
        ]

        with (
            patch.object(settings, "RERANKER_BINDING", "bge"),
            patch("src.services.rag._reranker._rerank_bge") as mock_bge,
        ):
            mock_bge.return_value = [docs[1], docs[0]]  # Reversed

            results = rag.rerank("query", docs, 2)

            mock_bge.assert_called_once()
            assert results == [docs[1], docs[0]]

    def test_rerank_no_binding_returns_original(self):
        """Should return original docs with reranked=False when no reranker configured."""
        docs = [
            {"content": "doc 1", "score": 0.8},
            {"content": "doc 2", "score": 0.6},
            {"content": "doc 3", "score": 0.4},
        ]

        with patch.object(settings, "RERANKER_BINDING", ""):
            results = rag.rerank("query", docs, 5)
            assert results == [{**d, "reranked": False} for d in docs]
