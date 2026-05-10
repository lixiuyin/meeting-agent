"""Contract tests for file scoping strategies.

Each test verifies a strategy's select_scope returns a ScopeSelection
with the expected structure and semantics.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.rag._scope_types import ScopeSelection
from src.services.rag._scoping_strategies import (
    FunnelOnlyStrategy,
    RouterAndFunnelStrategy,
    RouterOnlyStrategy,
    RouterPreFilterStrategy,
    get_scoping_strategy,
)

# Routing helpers are imported into the strategy module's namespace, so
# patches must target that namespace (where the names are bound) rather
# than the originating module.
_SCOPING = "src.services.rag._scoping_strategies"


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.RAG_FILE_SCOPING_MODE = "router_and_funnel"
    s.RAG_FUNNEL_NARROW_MIN_EVIDENCE = 0.15
    s.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = True
    s.RAG_ANCHOR_QUOTA_RATIO = 0.5
    s.RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK = True
    return s


# ---------------------------------------------------------------------------
# get_scoping_strategy dispatch
# ---------------------------------------------------------------------------
class TestGetScopingStrategy:
    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown RAG_FILE_SCOPING_MODE"):
            get_scoping_strategy("nonexistent")

    def test_all_modes_resolved(self) -> None:
        for mode in ("router_and_funnel", "funnel_only", "router_pre_filter", "router_only"):
            strategy = get_scoping_strategy(mode)
            assert strategy.name == mode


# ---------------------------------------------------------------------------
# RouterAndFunnelStrategy
# ---------------------------------------------------------------------------
class TestRouterAndFunnelStrategy:
    @pytest.mark.asyncio
    async def test_returns_scope_selection(self) -> None:
        strategy = RouterAndFunnelStrategy()
        selection = ScopeSelection(scope_file_ids=[1, 2], file_scores={1: 0.9, 2: 0.8})
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=[(1, 0.9), (2, 0.8)],
            ),
            patch(f"{_SCOPING}.narrow_scope_via_funnel", return_value=selection),
        ):
            sel = await strategy.select_scope("test query", None, None, None, 8, None, None, None)
        assert isinstance(sel, ScopeSelection)
        assert sel.scope_file_ids == [1, 2]
        assert sel.file_scores == {1: 0.9, 2: 0.8}

    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        strategy = RouterAndFunnelStrategy()
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(f"{_SCOPING}.narrow_scope_via_funnel", return_value=ScopeSelection()),
        ):
            sel = await strategy.select_scope("test query", None, None, None, 8, None, None, None)
        assert sel.scope_file_ids == []


# ---------------------------------------------------------------------------
# FunnelOnlyStrategy
# ---------------------------------------------------------------------------
class TestFunnelOnlyStrategy:
    @pytest.mark.asyncio
    async def test_no_router_scope(self) -> None:
        strategy = FunnelOnlyStrategy()
        selection = ScopeSelection(scope_file_ids=[3, 4], file_scores={3: 0.7, 4: 0.6})
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(f"{_SCOPING}.narrow_scope_via_funnel", return_value=selection) as mock_narrow,
        ):
            sel = await strategy.select_scope("test", [10], [20], [30], 8, None, None, None)
        assert sel.scope_file_ids == [3, 4]
        call_args = mock_narrow.call_args[0]
        assert call_args[1] is None  # router_scope=None

    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        strategy = FunnelOnlyStrategy()
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(f"{_SCOPING}.narrow_scope_via_funnel", return_value=ScopeSelection()),
        ):
            sel = await strategy.select_scope("test", None, None, None, 8, None, None, None)
        assert sel.scope_file_ids == []


# ---------------------------------------------------------------------------
# RouterPreFilterStrategy
# ---------------------------------------------------------------------------
class TestRouterPreFilterStrategy:
    @pytest.mark.asyncio
    async def test_prefilters_meetings(self) -> None:
        strategy = RouterPreFilterStrategy()
        selection = ScopeSelection(scope_file_ids=[5], file_scores={5: 0.9})
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}.router_prefilter_meetings", new_callable=AsyncMock, return_value=[1, 2]
            ),
            patch(f"{_SCOPING}.narrow_scope_via_funnel", return_value=selection) as mock_narrow,
        ):
            sel = await strategy.select_scope("test", None, None, None, 8, None, None, None)
        assert sel.scope_file_ids == [5]
        call_args = mock_narrow.call_args[0]
        # narrow_scope_via_funnel args: (query, router_scope, meeting_ids, ...)
        assert set(call_args[2]) == {1, 2}

    @pytest.mark.asyncio
    async def test_no_router_scope_passed(self) -> None:
        strategy = RouterPreFilterStrategy()
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}.router_prefilter_meetings", new_callable=AsyncMock, return_value=None
            ),
            patch(
                f"{_SCOPING}.narrow_scope_via_funnel", return_value=ScopeSelection()
            ) as mock_narrow,
        ):
            await strategy.select_scope("test", None, None, None, 8, None, None, None)
        call_args = mock_narrow.call_args[0]
        assert call_args[1] is None  # router_scope=None


# ---------------------------------------------------------------------------
# RouterOnlyStrategy
# ---------------------------------------------------------------------------
class TestRouterOnlyStrategy:
    @pytest.mark.asyncio
    async def test_scored_router_selects_files(self) -> None:
        strategy = RouterOnlyStrategy()
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=[(1, 0.9), (2, 0.8)],
            ),
        ):
            sel = await strategy.select_scope("test", None, None, None, 8, None, None, None)
        assert sel.scope_file_ids == [1, 2]
        assert sel.file_scores == {1: 0.9, 2: 0.8}

    @pytest.mark.asyncio
    async def test_router_empty_enumerates(self) -> None:
        strategy = RouterOnlyStrategy()
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                f"{_SCOPING}._enumerate_scope_files", new_callable=AsyncMock, return_value=[10, 20]
            ),
        ):
            sel = await strategy.select_scope("test", None, None, None, 8, None, None, None)
        assert sel.scope_file_ids == [10, 20]

    @pytest.mark.asyncio
    async def test_anchor_injection_with_cap_evict(self) -> None:
        strategy = RouterOnlyStrategy()
        mock_settings = _make_settings()
        mock_settings.RAG_ANCHOR_QUOTA_RATIO = 0.5
        with (
            patch(f"{_SCOPING}.settings", mock_settings),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=[(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)],
            ),
        ):
            sel = await strategy.select_scope("test", None, None, [99], 8, None, None, None)
        assert 99 in sel.scope_file_ids
        assert len(sel.scope_file_ids) <= 4 + 2

    @pytest.mark.asyncio
    async def test_anchor_already_in_scope(self) -> None:
        strategy = RouterOnlyStrategy()
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=[(1, 0.9), (2, 0.8)],
            ),
        ):
            sel = await strategy.select_scope("test", None, None, [1], 8, None, None, None)
        assert sel.scope_file_ids.count(1) == 1

    @pytest.mark.asyncio
    async def test_anchor_disabled(self) -> None:
        strategy = RouterOnlyStrategy()
        mock_settings = _make_settings()
        mock_settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL = False
        with (
            patch(f"{_SCOPING}.settings", mock_settings),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=[(1, 0.9)],
            ),
        ):
            sel = await strategy.select_scope("test", None, None, [99], 8, None, None, None)
        assert 99 not in sel.scope_file_ids

    @pytest.mark.asyncio
    async def test_scored_router_returns_none_falls_back(self) -> None:
        strategy = RouterOnlyStrategy()
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                f"{_SCOPING}._route_scope_files_via_summary",
                new_callable=AsyncMock,
                return_value=[5, 6],
            ),
        ):
            sel = await strategy.select_scope("test", None, None, None, 8, None, None, None)
        assert sel.scope_file_ids == [5, 6]

    @pytest.mark.asyncio
    async def test_all_routers_fail_enumerates(self) -> None:
        strategy = RouterOnlyStrategy()
        with (
            patch(f"{_SCOPING}.settings", _make_settings()),
            patch(
                f"{_SCOPING}._route_scope_files_with_scores",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                f"{_SCOPING}._route_scope_files_via_summary",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(f"{_SCOPING}._enumerate_scope_files", new_callable=AsyncMock, return_value=[100]),
        ):
            sel = await strategy.select_scope("test", None, None, None, 8, None, None, None)
        assert sel.scope_file_ids == [100]


# ---------------------------------------------------------------------------
# ScopeSelection dataclass
# ---------------------------------------------------------------------------
class TestScopeSelection:
    def test_defaults(self) -> None:
        sel = ScopeSelection()
        assert sel.scope_file_ids == []
        assert sel.file_scores == {}
        assert sel.docs_by_file == {}

    def test_frozen(self) -> None:
        sel = ScopeSelection(scope_file_ids=[1])
        with pytest.raises(AttributeError):
            sel.scope_file_ids = [2]  # type: ignore[misc]
