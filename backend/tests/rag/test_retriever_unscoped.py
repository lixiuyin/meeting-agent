"""Tests for unscoped retrieval widening and reranker soft threshold."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.core.config import settings  # noqa: E402
from src.services.rag._query import determine_adaptive_top_k  # noqa: E402
from src.services.rag._reranker import rerank  # noqa: E402


class TestDetermineAdaptiveTopKUnscoped:
    """Verify unscoped queries get a raised floor."""

    def test_unscoped_short_question_gets_raised_floor(self):
        """Short question with no scope should get at least 8."""
        with patch.object(settings, "TOP_K", 5):
            k = determine_adaptive_top_k("AI", None, is_broad_recall=True)
            assert k >= 8

    def test_unscoped_normal_question_respects_top_k(self):
        """Normal-length unscoped question returns max(TOP_K, 8)."""
        with patch.object(settings, "TOP_K", 10):
            k = determine_adaptive_top_k(
                "What are the main technical points discussed?", None, is_broad_recall=True
            )
            assert k == 10

    def test_scoped_short_question_still_gets_3(self):
        """Scoped short questions still use the lower floor (existing behavior)."""
        with patch.object(settings, "TOP_K", 5):
            k = determine_adaptive_top_k("AI", None, is_broad_recall=False)
            assert k == 3

    def test_user_override_always_wins(self):
        """User-requested k should always be used regardless of scope."""
        k = determine_adaptive_top_k("short", 20, is_broad_recall=True)
        assert k == 20
        k2 = determine_adaptive_top_k("short", 1, is_broad_recall=False)
        assert k2 == 1


class TestRerankerSoftThreshold:
    """Verify unscoped queries use softer reranker threshold."""

    def test_unscoped_uses_lower_threshold(self):
        """Unscoped queries should use min(RERANKER_MIN_SCORE, 0.05)."""
        docs = [
            {"content": "doc1", "score": 0.03, "reranked": True},
            {"content": "doc2", "score": 0.10, "reranked": True},
            {"content": "doc3", "score": 0.50, "reranked": True},
        ]
        with (
            patch.object(settings, "RERANKER_BINDING", ""),
        ):
            # Binding empty => returns docs as-is with reranked=False
            result = rerank("query", docs, is_unscoped=True)
            assert len(result) == 3

    def test_scoped_uses_normal_threshold(self):
        """Scoped queries use the full RERANKER_MIN_SCORE."""
        docs = [
            {"content": "doc1", "score": 0.10, "reranked": True},
            {"content": "doc2", "score": 0.50, "reranked": True},
        ]
        with (
            patch.object(settings, "RERANKER_BINDING", ""),
        ):
            result = rerank("query", docs, is_unscoped=False)
            assert len(result) == 2

    def test_unscoped_guarantees_top_n_when_all_below_threshold(self):
        """When all reranked scores are below soft threshold, guarantee top_n docs."""
        docs = [{"content": f"doc{i}", "score": 0.01, "reranked": True} for i in range(10)]
        with (
            patch.object(settings, "RERANKER_BINDING", "cohere"),
            patch.object(settings, "RERANKER_MIN_SCORE", 0.15),
            patch("src.services.rag._reranker._rerank_cohere") as mock_cohere,
        ):
            mock_cohere.return_value = docs[:5]
            result = rerank("query", docs, top_n=5, is_unscoped=True)
            assert len(result) > 0  # Guaranteed non-empty even below threshold

    def test_empty_docs_returns_empty(self):
        with (
            patch.object(settings, "RERANKER_BINDING", ""),
        ):
            assert rerank("query", []) == []
