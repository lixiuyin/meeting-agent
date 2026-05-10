"""Chaos: 1000 files, 90% low-score long tail — funnel narrow must recall high-relevance files."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.rag._funnel_narrow import narrow_scope_via_funnel
from src.services.rag._scope_types import ScopeSelection

pytestmark = pytest.mark.chaos


def _make_doc(file_id: int, score: float, meeting_id: int = 1) -> dict:
    return {
        "content": f"file_{file_id} content",
        "metadata": {"file_id": file_id, "meeting_id": meeting_id, "chunk_index": 0},
        "score": score,
    }


def _make_settings(target_files: int = 8):
    s = MagicMock()
    s.TOP_K = 10
    s.RAG_FUNNEL_FETCH_MULTIPLIER = 3
    s.RAG_ANCHOR_ENABLED = False
    s.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = False
    s.RAG_ANCHOR_QUOTA_RATIO = 0.5
    s.RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO = 0.8
    s.RAG_FUNNEL_MERGE_STRATEGY = "rrf"
    s.RAG_FUNNEL_RRF_K = 60
    s.RAG_FUNNEL_NARROW_MIN_EVIDENCE = 0.15
    s.RAG_FUNNEL_AGGREGATION = "top_k_mean"
    s.RAG_FUNNEL_AGG_TOP_K = 3
    s.RAG_FUNNEL_AGGREGATION_ALPHA = 1.0
    s.RAG_FUNNEL_FILE_PRIOR_ENABLED = False
    s.RAG_FUNNEL_FILE_PRIOR_FULL_MATCH_BONUS = 0.0
    s.RAG_FUNNEL_EVIDENCE_MODE = "absolute"
    s.RAG_FUNNEL_WIDE_K_MIN = 0
    s.RAG_FUNNEL_WIDE_K_MAX = 0
    s.RAG_FUNNEL_MULTIMODAL_ENABLED = False
    s.RAGANYTHING_ENABLED = False
    s.RAG_BROAD_RECALL_SCOPE_CAP = target_files
    return s


class TestManyFilesLongTail:
    """Verify funnel narrow recovers high-scoring files from a long-tail distribution."""

    def test_high_score_files_recalled_from_1000_files(self) -> None:
        """1000 files: 3 high-score, 997 low-score. Top 3 must appear in scope."""
        target_files = 8
        high_score_file_ids = [100, 200, 300]
        docs: list[dict] = []

        # 3 high-score files with multiple high-score chunks each
        for fid in high_score_file_ids:
            for ci in range(10):
                d = _make_doc(fid, score=0.9 - ci * 0.01)
                d["metadata"]["chunk_index"] = ci
                docs.append(d)

        # 997 low-score files with low-score chunks
        for fid in range(1, 1001):
            if fid in high_score_file_ids:
                continue
            for ci in range(3):
                d = _make_doc(fid, score=0.05 + (fid % 10) * 0.001)
                d["metadata"]["chunk_index"] = ci
                docs.append(d)

        mock_settings = _make_settings(target_files)

        with (
            patch("src.services.rag._funnel_narrow.retrieve", return_value=(docs, MagicMock())),
            patch(
                "src.services.rag._funnel_narrow._vector_score_lower_is_better", return_value=False
            ),
            patch("src.services.rag._funnel_narrow.settings", mock_settings),
        ):
            result = narrow_scope_via_funnel(
                "test query",
                router_scope=None,
                meeting_ids=None,
                anchor_meeting_ids=None,
                anchor_file_ids=None,
                trace=None,
                rag_mode=None,
                known_speakers=None,
                target_files=target_files,
                min_chunk_evidence=0.15,
            )

        assert isinstance(result, ScopeSelection)
        for fid in high_score_file_ids:
            assert fid in result.scope_file_ids, (
                f"High-score file {fid} missing from scope {result.scope_file_ids}"
            )

    def test_scope_respects_target_files_cap(self) -> None:
        """Even with 1000 files producing chunks, scope must not exceed target."""
        target_files = 6
        docs: list[dict] = []
        for fid in range(1, 1001):
            for ci in range(5):
                d = _make_doc(fid, score=0.3 + (fid % 100) * 0.005)
                d["metadata"]["chunk_index"] = ci
                docs.append(d)

        mock_settings = _make_settings(target_files)

        with (
            patch("src.services.rag._funnel_narrow.retrieve", return_value=(docs, MagicMock())),
            patch(
                "src.services.rag._funnel_narrow._vector_score_lower_is_better", return_value=False
            ),
            patch("src.services.rag._funnel_narrow.settings", mock_settings),
        ):
            result = narrow_scope_via_funnel(
                "test query",
                router_scope=None,
                meeting_ids=None,
                anchor_meeting_ids=None,
                anchor_file_ids=None,
                trace=None,
                rag_mode=None,
                known_speakers=None,
                target_files=target_files,
                min_chunk_evidence=0.15,
            )

        assert isinstance(result, ScopeSelection)
        assert len(result.scope_file_ids) <= target_files

    def test_all_identical_low_scores_returns_non_empty(self) -> None:
        """Even when all chunks have identical low scores, scope should not be empty."""
        target_files = 5
        docs: list[dict] = []
        for fid in range(1, 51):
            for ci in range(3):
                d = _make_doc(fid, score=0.2)
                d["metadata"]["chunk_index"] = ci
                docs.append(d)

        mock_settings = _make_settings(target_files)
        mock_settings.RAG_FUNNEL_NARROW_MIN_EVIDENCE = 0.1

        with (
            patch("src.services.rag._funnel_narrow.retrieve", return_value=(docs, MagicMock())),
            patch(
                "src.services.rag._funnel_narrow._vector_score_lower_is_better", return_value=False
            ),
            patch("src.services.rag._funnel_narrow.settings", mock_settings),
        ):
            result = narrow_scope_via_funnel(
                "test query",
                router_scope=None,
                meeting_ids=None,
                anchor_meeting_ids=None,
                anchor_file_ids=None,
                trace=None,
                rag_mode=None,
                known_speakers=None,
                target_files=target_files,
                min_chunk_evidence=0.1,
            )

        assert isinstance(result, ScopeSelection)
        assert len(result.scope_file_ids) > 0
