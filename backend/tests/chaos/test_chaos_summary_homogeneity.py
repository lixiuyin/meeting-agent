"""Chaos: all file summaries nearly identical — router must still produce valid output."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.services.rag._scoping_strategies import RouterOnlyStrategy, ScopeSelection

pytestmark = pytest.mark.chaos


class TestSummaryHomogeneity:
    """When summary embeddings are nearly identical, router must not crash or return empty."""

    @pytest.mark.asyncio
    async def test_homogeneous_scores_produces_valid_scope(self) -> None:
        """All scores in [0.70, 0.72] — router must still return a scope."""
        # Build 20 files with nearly identical scores
        scored = [(fid, 0.70 + (fid % 5) * 0.005) for fid in range(1, 21)]

        with (
            patch(
                "src.services.rag._scoping_strategies._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=scored,
            ),
            patch("src.services.rag._scoping_strategies.settings") as mock_settings,
        ):
            mock_settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = False
            mock_settings.RAG_ANCHOR_QUOTA_RATIO = 0.5
            mock_settings.RAG_BROAD_RECALL_SCOPE_CAP = 8

            strategy = RouterOnlyStrategy()
            result = await strategy.select_scope(
                primary_query="what were the key decisions?",
                meeting_ids=None,
                anchor_meeting_ids=None,
                anchor_file_ids=None,
                target_files=8,
                trace=None,
                rag_mode=None,
                known_speakers=None,
            )

        assert isinstance(result, ScopeSelection)
        assert len(result.scope_file_ids) > 0
        # RouterOnlyStrategy passes through all scored files without truncation
        expected_ids = {fid for fid, _ in scored}
        assert set(result.scope_file_ids).issubset(expected_ids)

    @pytest.mark.asyncio
    async def test_identical_scores_does_not_error(self) -> None:
        """All scores exactly 0.71 — no division-by-zero or ordering errors."""
        scored = [(fid, 0.71) for fid in range(1, 11)]

        with (
            patch(
                "src.services.rag._scoping_strategies._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=scored,
            ),
            patch("src.services.rag._scoping_strategies.settings") as mock_settings,
        ):
            mock_settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = False
            mock_settings.RAG_ANCHOR_QUOTA_RATIO = 0.5
            mock_settings.RAG_BROAD_RECALL_SCOPE_CAP = 5

            strategy = RouterOnlyStrategy()
            result = await strategy.select_scope(
                primary_query="quarterly review",
                meeting_ids=None,
                anchor_meeting_ids=None,
                anchor_file_ids=None,
                target_files=5,
                trace=None,
                rag_mode=None,
                known_speakers=None,
            )

        assert isinstance(result, ScopeSelection)
        # RouterOnlyStrategy passes through all scored files without truncation
        assert len(result.scope_file_ids) > 0

    @pytest.mark.asyncio
    async def test_router_empty_with_enumerate_fallback(self) -> None:
        """Router returns [] (no summary match) — enumerate fallback kicks in."""
        with (
            patch(
                "src.services.rag._scoping_strategies._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.services.rag._scoping_strategies._enumerate_scope_files",
                new_callable=AsyncMock,
                return_value=[10, 20, 30],
            ) as mock_enum,
            patch("src.services.rag._scoping_strategies.settings") as mock_settings,
        ):
            mock_settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = False
            mock_settings.RAG_ANCHOR_QUOTA_RATIO = 0.5
            mock_settings.RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK = True
            mock_settings.RAG_BROAD_RECALL_SCOPE_CAP = 8

            strategy = RouterOnlyStrategy()
            result = await strategy.select_scope(
                primary_query="unrelated topic",
                meeting_ids=None,
                anchor_meeting_ids=None,
                anchor_file_ids=None,
                target_files=8,
                trace=None,
                rag_mode=None,
                known_speakers=None,
            )

        mock_enum.assert_awaited_once()
        assert isinstance(result, ScopeSelection)
        assert len(result.scope_file_ids) == 3
