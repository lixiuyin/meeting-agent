"""Tests for unified multi-query + broad recall / scoped retrieval (v4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.trace import TraceContext
from src.services.rag._scope_types import ScopeSelection

# Module paths after refactor — functions moved to sub-modules.
_STEPS = "src.services.chain._steps_retrieve"
_ROUTING = "src.services.chain._retrieve_routing"
_UTILS = "src.services.chain._retrieve_utils"
_BROAD = "src.services.chain._retrieve_broad"
_SCOPING = "src.services.rag._scoping_strategies"


def _make_ctx(
    *,
    file_ids: list[int] | None = None,
    meeting_ids: list[int] | None = None,
    question: str = "test question",
    session_id: str | None = None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.file_ids = file_ids
    ctx.meeting_ids = meeting_ids
    ctx.question = question
    ctx.rewritten_query = None
    ctx.top_k = 8
    ctx.file_types = None
    ctx.date_from = None
    ctx.date_to = None
    ctx.trace = TraceContext()
    ctx.rag_mode = None
    ctx.docs = []
    ctx.scope_file_ids = []
    ctx.session_id = session_id
    return ctx


_DEFAULT_SETTINGS = {
    "RAG_RERANK_FETCH_MULTIPLIER": 1,
    "RERANKER_BINDING": "",
    "TOP_K": 8,
    "RAG_ANCHOR_ENABLED": False,
    "RAG_SIBLING_CORETRIEVE_ENABLED": False,
    "MULTI_QUERY_ENABLED": False,
    "MULTI_QUERY_COUNT": 3,
    "RAG_BROAD_RECALL_MULTI_QUERY_ENABLED": True,
    "HYBRID_SEARCH_ENABLED": False,
    "RAG_FUNNEL_NARROW_MIN_EVIDENCE": 0.15,
    "RAG_BROAD_RECALL_SCOPE_CAP": 8,
    "BROAD_RECALL_MAX_FILES": 50,
    "RAG_FAIR_ADAPTIVE_CHUNKS": False,
    "RAG_MIN_CHUNKS_PER_FILE": 2,
    "RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK": True,
    "RAG_ANCHOR_BOOST_IN_BROAD_RECALL": True,
    "RAG_ANCHOR_QUOTA_RATIO": 0.5,
    "SUMMARY_INTENT_TOP_K": 20,
    "RAG_HIERARCHICAL_ENABLED": False,
    "RAG_FILE_SCOPING_MODE": "router_and_funnel",
}


def _apply_settings(mock_settings: MagicMock) -> None:
    for key, val in _DEFAULT_SETTINGS.items():
        setattr(mock_settings, key, val)


@pytest.mark.asyncio
async def test_multi_query_broad_recall_uses_funnel_narrow() -> None:
    from src.services.chain._steps_retrieve import retrieve_documents

    ctx = _make_ctx(file_ids=None)

    docs_by_file = {1: [{"content": "cached", "metadata": {"file_id": 1}, "score": 0.6}]}

    with (
        patch(f"{_STEPS}.settings") as mock_settings,
        patch(f"{_ROUTING}._load_known_speakers", return_value=[]),
        patch(f"{_STEPS}._read_anchor", return_value=(None, None)),
        patch(f"{_STEPS}.determine_adaptive_top_k", return_value=8),
        patch(f"{_STEPS}._is_simple_query", return_value=False),
        patch(
            f"{_UTILS}._generate_query_variants",
            new_callable=AsyncMock,
            return_value=["variant_a", "variant_b"],
        ),
        patch(f"{_STEPS}.retrieve_sibling_chunks", return_value=[]),
        patch(
            f"{_SCOPING}._route_scope_files_with_scores",
            new_callable=AsyncMock,
            return_value=[(1, 0.9)],
        ),
        patch(
            f"{_SCOPING}.wide_fetch_for_funnel",
            return_value=[],
        ),
        patch(
            f"{_SCOPING}.narrow_scope_via_funnel",
            return_value=ScopeSelection(
                scope_file_ids=[1],
                file_scores={1: 0.9},
                docs_by_file=docs_by_file,
            ),
        ) as mock_narrow,
        patch(
            f"{_BROAD}.fair_retrieve_per_file",
            new_callable=AsyncMock,
            return_value=[{"content": "c", "metadata": {}, "score": 0.5}],
        ) as mock_fair,
    ):
        _apply_settings(mock_settings)
        mock_settings.MULTI_QUERY_ENABLED = True
        await retrieve_documents(ctx)

    assert mock_narrow.call_count == 3
    assert mock_fair.call_count == 3
    for call in mock_fair.call_args_list:
        assert call.kwargs.get("cached_docs") == docs_by_file


@pytest.mark.asyncio
async def test_broad_recall_skips_multi_query_by_default() -> None:
    """When RAG_BROAD_RECALL_MULTI_QUERY_ENABLED=False, broad recall must
    not generate query variants — only the single primary query is used."""
    from src.services.chain._steps_retrieve import retrieve_documents

    ctx = _make_ctx(file_ids=None)

    with (
        patch(f"{_STEPS}.settings") as mock_settings,
        patch(f"{_ROUTING}._load_known_speakers", return_value=[]),
        patch(f"{_STEPS}._read_anchor", return_value=(None, None)),
        patch(f"{_STEPS}.determine_adaptive_top_k", return_value=8),
        patch(f"{_STEPS}._is_simple_query", return_value=False),
        patch(
            f"{_UTILS}._generate_query_variants",
            new_callable=AsyncMock,
        ) as mock_variants,
        patch(f"{_STEPS}.retrieve_sibling_chunks", return_value=[]),
        patch(
            f"{_SCOPING}._route_scope_files_with_scores",
            new_callable=AsyncMock,
            return_value=[(1, 0.9)],
        ),
        patch(
            f"{_SCOPING}.narrow_scope_via_funnel",
            return_value=ScopeSelection(scope_file_ids=[1], file_scores={1: 0.9}),
        ),
        patch(
            f"{_BROAD}.fair_retrieve_per_file",
            new_callable=AsyncMock,
            return_value=[{"content": "c", "metadata": {}, "score": 0.5}],
        ) as mock_fair,
    ):
        _apply_settings(mock_settings)
        mock_settings.MULTI_QUERY_ENABLED = True
        mock_settings.RAG_BROAD_RECALL_MULTI_QUERY_ENABLED = False
        await retrieve_documents(ctx)

    mock_variants.assert_not_called()
    assert mock_fair.call_count == 1


@pytest.mark.asyncio
async def test_multi_query_scoped_uses_retrieve_fn() -> None:
    from src.services.chain._steps_retrieve import retrieve_documents

    ctx = _make_ctx(file_ids=[1])

    mock_retrieve = MagicMock(return_value=([], MagicMock()))

    with (
        patch(f"{_STEPS}.settings") as mock_settings,
        patch(f"{_ROUTING}._load_known_speakers", return_value=[]),
        patch(f"{_STEPS}._read_anchor", return_value=(None, None)),
        patch(f"{_STEPS}.determine_adaptive_top_k", return_value=8),
        patch(f"{_STEPS}._is_simple_query", return_value=False),
        patch(
            f"{_UTILS}._generate_query_variants",
            new_callable=AsyncMock,
            return_value=["variant_a"],
        ),
        patch(f"{_STEPS}.retrieve_sibling_chunks", return_value=[]),
        patch(f"{_BROAD}.retrieve", mock_retrieve),
    ):
        _apply_settings(mock_settings)
        mock_settings.MULTI_QUERY_ENABLED = True
        await retrieve_documents(ctx)

    assert mock_retrieve.call_count == 2


@pytest.mark.asyncio
async def test_empty_scope_returns_empty() -> None:
    from src.services.chain._steps_retrieve import retrieve_documents

    ctx = _make_ctx(file_ids=None)

    with (
        patch(f"{_STEPS}.settings") as mock_settings,
        patch(f"{_ROUTING}._load_known_speakers", return_value=[]),
        patch(f"{_STEPS}._read_anchor", return_value=(None, None)),
        patch(f"{_STEPS}.determine_adaptive_top_k", return_value=8),
        patch(f"{_STEPS}.retrieve_sibling_chunks", return_value=[]),
        patch(
            f"{_SCOPING}._route_scope_files_with_scores",
            new_callable=AsyncMock,
            return_value=[(1, 0.9)],
        ),
        patch(
            f"{_SCOPING}.narrow_scope_via_funnel",
            return_value=ScopeSelection(),
        ),
    ):
        _apply_settings(mock_settings)
        await retrieve_documents(ctx)

    assert ctx.docs == []


@pytest.mark.asyncio
async def test_router_only_mode_skips_funnel_narrow() -> None:
    from src.services.chain._steps_retrieve import retrieve_documents

    ctx = _make_ctx(file_ids=None)

    with (
        patch(f"{_STEPS}.settings") as mock_settings,
        patch(f"{_ROUTING}._load_known_speakers", return_value=[]),
        patch(f"{_STEPS}._read_anchor", return_value=(None, None)),
        patch(f"{_STEPS}.determine_adaptive_top_k", return_value=8),
        patch(f"{_STEPS}.retrieve_sibling_chunks", return_value=[]),
        patch(
            f"{_SCOPING}._route_scope_files_with_scores",
            new_callable=AsyncMock,
            return_value=[(1, 0.9)],
        ),
        patch("src.services.rag._funnel_narrow.narrow_scope_via_funnel") as mock_narrow,
        patch(
            f"{_BROAD}.fair_retrieve_per_file",
            new_callable=AsyncMock,
            return_value=[{"content": "c", "metadata": {}, "score": 0.5}],
        ),
    ):
        _apply_settings(mock_settings)
        mock_settings.RAG_FILE_SCOPING_MODE = "router_only"
        await retrieve_documents(ctx)

    mock_narrow.assert_not_called()


# ---------------------------------------------------------------------------
# B3 regression: docs_by_file dedup key must include file_id
# ---------------------------------------------------------------------------


def test_docs_by_file_dedup_key_includes_file_id() -> None:
    """Docs from different files with the same chunk_index must not be deduped.

    Before the B3 fix the key was ``chunk_index:content[:80]``, which
    collapsed identical chunk positions across files.  The fix prepends
    file_id so each file's chunks are independently tracked.
    """
    doc_f1 = {"content": "shared header text", "metadata": {"file_id": 1, "chunk_index": 0}}
    doc_f2 = {"content": "shared header text", "metadata": {"file_id": 2, "chunk_index": 0}}

    merged: dict[int, list[dict]] = {}

    for fid, doc in [(1, doc_f1), (2, doc_f2)]:
        existing = merged.setdefault(fid, [])
        seen = {
            f"{(d.get('metadata') or {}).get('file_id')}:"
            f"{(d.get('metadata') or {}).get('chunk_index')}:"
            f"{(d.get('content') or '')[:80]}"
            for d in existing
        }
        key = (
            f"{(doc.get('metadata') or {}).get('file_id')}:"
            f"{(doc.get('metadata') or {}).get('chunk_index')}:"
            f"{(doc.get('content') or '')[:80]}"
        )
        if key not in seen:
            seen.add(key)
            existing.append(doc)

    assert len(merged[1]) == 1, "file 1 chunk must be present"
    assert len(merged[2]) == 1, "file 2 chunk must not be dropped as duplicate of file 1"


def test_docs_by_file_dedup_same_file_same_chunk_across_variants() -> None:
    """The same chunk appearing in two query-variant selections is deduped correctly."""
    doc = {"content": "repeated chunk", "metadata": {"file_id": 1, "chunk_index": 0}}

    merged: dict[int, list[dict]] = {}

    for _ in range(2):  # simulate two variants both returning the same chunk
        fid = 1
        existing = merged.setdefault(fid, [])
        seen = {
            f"{(d.get('metadata') or {}).get('file_id')}:"
            f"{(d.get('metadata') or {}).get('chunk_index')}:"
            f"{(d.get('content') or '')[:80]}"
            for d in existing
        }
        key = (
            f"{(doc.get('metadata') or {}).get('file_id')}:"
            f"{(doc.get('metadata') or {}).get('chunk_index')}:"
            f"{(doc.get('content') or '')[:80]}"
        )
        if key not in seen:
            seen.add(key)
            existing.append(doc)

    assert len(merged[1]) == 1, "same chunk from two variants must be deduplicated"
