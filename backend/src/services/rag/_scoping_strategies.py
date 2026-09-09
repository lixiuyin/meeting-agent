"""File scoping strategies for broad recall mode.

Each strategy determines how files are selected when the user has not
explicitly chosen files.  The active strategy is selected via
``settings.RAG_FILE_SCOPING_MODE``.

Strategies:
  router_and_funnel — summary router + funnel parallel, zigzag merge (default)
  funnel_only       — skip summary router, funnel does all file selection
  router_pre_filter — router narrows meeting scope, funnel within those meetings
  router_only       — router selects files directly (no funnel narrow)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from ...core.config import settings
from ._anchor_inject import apply_anchor_evict
from ._funnel_narrow import narrow_scope_via_funnel, wide_fetch_for_funnel
from ._routing import (
    _enumerate_scope_files,
    _route_scope_files_via_summary,
    _route_scope_files_with_scores,
    router_prefilter_meetings,
)
from ._scope_types import BroadRecallContext, ScopeSelection

if TYPE_CHECKING:
    from ...core.trace import TraceContext

logger = logging.getLogger(__name__)


async def _shared_wide_fetch(
    broad_recall_ctx: BroadRecallContext | None,
    primary_query: str,
    meeting_ids: list[int] | None,
    anchor_meeting_ids: list[int] | None,
    trace: TraceContext | None,
    rag_mode: str | None,
    known_speakers: list[str] | None,
    user_id: str | None,
) -> list[dict]:
    """Wide fetch with optional request-scoped caching for multi-query.

    Without a context, runs the wide fetch directly.  With a context, the
    first variant to request a scope key performs the actual Chroma search;
    concurrent variants with the same scope reuse the cached doc pool.
    """
    if broad_recall_ctx is None:
        return await asyncio.to_thread(
            wide_fetch_for_funnel,
            primary_query,
            meeting_ids,
            anchor_meeting_ids,
            trace,
            rag_mode,
            known_speakers,
            user_id=user_id,
        )
    key = broad_recall_ctx.make_key(meeting_ids, anchor_meeting_ids, primary_query)
    return await broad_recall_ctx.get_or_compute(
        key,
        lambda: asyncio.to_thread(
            wide_fetch_for_funnel,
            primary_query,
            meeting_ids,
            anchor_meeting_ids,
            trace,
            rag_mode,
            known_speakers,
            user_id=user_id,
        ),
    )


class FileScopingStrategy(Protocol):
    """Protocol for file scoping strategies."""

    name: str

    async def select_scope(
        self,
        primary_query: str,
        meeting_ids: list[int] | None,
        anchor_meeting_ids: list[int] | None,
        anchor_file_ids: list[int] | None,
        target_files: int,
        trace: TraceContext | None,
        rag_mode: str | None,
        known_speakers: list[str] | None,
        *,
        summary_intent: bool = False,
        broad_recall_ctx: BroadRecallContext | None = None,
        user_id: str | None = None,
    ) -> ScopeSelection: ...


class RouterAndFunnelStrategy:
    """Summary router + funnel parallel, RRF merge (v4 default).

    M4: router and the funnel's wide-fetch are now launched concurrently —
    the router runs as a coroutine on the event loop while wide-fetch runs
    in a worker thread.  When both finish we re-enter the funnel narrow
    sync path with the prefetched docs to keep the existing aggregation
    semantics (including anchor injection and metric emission) unchanged.
    """

    name = "router_and_funnel"

    async def select_scope(
        self,
        primary_query: str,
        meeting_ids: list[int] | None,
        anchor_meeting_ids: list[int] | None,
        anchor_file_ids: list[int] | None,
        target_files: int,
        trace: TraceContext | None,
        rag_mode: str | None,
        known_speakers: list[str] | None,
        *,
        summary_intent: bool = False,
        broad_recall_ctx: BroadRecallContext | None = None,
        user_id: str | None = None,
    ) -> ScopeSelection:
        router_task = asyncio.create_task(
            _route_scope_files_with_scores(
                primary_query,
                meeting_ids,
                trace=trace,
                user_id=user_id,
            )
        )
        wide_docs = await _shared_wide_fetch(
            broad_recall_ctx,
            primary_query,
            meeting_ids,
            anchor_meeting_ids,
            trace,
            rag_mode,
            known_speakers,
            user_id,
        )
        routed_with_scores = await router_task
        return await asyncio.to_thread(
            narrow_scope_via_funnel,
            primary_query,
            routed_with_scores,
            meeting_ids,
            anchor_meeting_ids,
            anchor_file_ids,
            trace,
            rag_mode,
            known_speakers,
            target_files=target_files,
            min_chunk_evidence=settings.RAG_FUNNEL_NARROW_MIN_EVIDENCE,
            prefetched_wide_docs=wide_docs,
            summary_intent=summary_intent,
            user_id=user_id,
        )


class FunnelOnlyStrategy:
    """Skip summary router — funnel is the sole file selector."""

    name = "funnel_only"

    async def select_scope(
        self,
        primary_query: str,
        meeting_ids: list[int] | None,
        anchor_meeting_ids: list[int] | None,
        anchor_file_ids: list[int] | None,
        target_files: int,
        trace: TraceContext | None,
        rag_mode: str | None,
        known_speakers: list[str] | None,
        *,
        summary_intent: bool = False,
        broad_recall_ctx: BroadRecallContext | None = None,
        user_id: str | None = None,
    ) -> ScopeSelection:
        if broad_recall_ctx is not None:
            wide_docs = await _shared_wide_fetch(
                broad_recall_ctx,
                primary_query,
                meeting_ids,
                anchor_meeting_ids,
                trace,
                rag_mode,
                known_speakers,
                user_id,
            )
            return await asyncio.to_thread(
                narrow_scope_via_funnel,
                primary_query,
                None,
                meeting_ids,
                anchor_meeting_ids,
                anchor_file_ids,
                trace,
                rag_mode,
                known_speakers,
                target_files=target_files,
                min_chunk_evidence=settings.RAG_FUNNEL_NARROW_MIN_EVIDENCE,
                prefetched_wide_docs=wide_docs,
                summary_intent=summary_intent,
                user_id=user_id,
            )
        return await asyncio.to_thread(
            narrow_scope_via_funnel,
            primary_query,
            None,
            meeting_ids,
            anchor_meeting_ids,
            anchor_file_ids,
            trace,
            rag_mode,
            known_speakers,
            target_files=target_files,
            min_chunk_evidence=settings.RAG_FUNNEL_NARROW_MIN_EVIDENCE,
            summary_intent=summary_intent,
            user_id=user_id,
        )


class RouterPreFilterStrategy:
    """Router narrows meeting scope, funnel operates within those meetings."""

    name = "router_pre_filter"

    async def select_scope(
        self,
        primary_query: str,
        meeting_ids: list[int] | None,
        anchor_meeting_ids: list[int] | None,
        anchor_file_ids: list[int] | None,
        target_files: int,
        trace: TraceContext | None,
        rag_mode: str | None,
        known_speakers: list[str] | None,
        *,
        summary_intent: bool = False,
        broad_recall_ctx: BroadRecallContext | None = None,
        user_id: str | None = None,
    ) -> ScopeSelection:
        pre_filter_meeting_ids = await router_prefilter_meetings(
            primary_query,
            meeting_ids,
            trace=trace,
            user_id=user_id,
        )
        if broad_recall_ctx is not None:
            wide_docs = await _shared_wide_fetch(
                broad_recall_ctx,
                primary_query,
                pre_filter_meeting_ids,
                anchor_meeting_ids,
                trace,
                rag_mode,
                known_speakers,
                user_id,
            )
            return await asyncio.to_thread(
                narrow_scope_via_funnel,
                primary_query,
                None,
                pre_filter_meeting_ids,
                anchor_meeting_ids,
                anchor_file_ids,
                trace,
                rag_mode,
                known_speakers,
                target_files=target_files,
                min_chunk_evidence=settings.RAG_FUNNEL_NARROW_MIN_EVIDENCE,
                prefetched_wide_docs=wide_docs,
                summary_intent=summary_intent,
                user_id=user_id,
            )
        return await asyncio.to_thread(
            narrow_scope_via_funnel,
            primary_query,
            None,
            pre_filter_meeting_ids,
            anchor_meeting_ids,
            anchor_file_ids,
            trace,
            rag_mode,
            known_speakers,
            target_files=target_files,
            min_chunk_evidence=settings.RAG_FUNNEL_NARROW_MIN_EVIDENCE,
            summary_intent=summary_intent,
            user_id=user_id,
        )


class RouterOnlyStrategy:
    """Router selects files directly — no funnel narrow, no wide fetch.

    Replaces the legacy ``funnel_narrow_in_broad_recall=false`` path.
    Uses the summary router for file selection and applies anchor boost
    with cap+evict semantics (consistent with funnel-based strategies).
    """

    name = "router_only"

    async def select_scope(
        self,
        primary_query: str,
        meeting_ids: list[int] | None,
        anchor_meeting_ids: list[int] | None,
        anchor_file_ids: list[int] | None,
        target_files: int,
        trace: TraceContext | None,
        rag_mode: str | None,
        known_speakers: list[str] | None,
        *,
        summary_intent: bool = False,
        broad_recall_ctx: BroadRecallContext | None = None,
        user_id: str | None = None,
    ) -> ScopeSelection:
        # Try scored router first
        routed_with_scores = await _route_scope_files_with_scores(
            primary_query,
            meeting_ids,
            trace=trace,
            user_id=user_id,
        )
        scope_file_ids: list[int] = []
        file_scores: dict[int, float] = {}

        if routed_with_scores:
            scope_file_ids = [fid for fid, _ in routed_with_scores]
            file_scores = dict(routed_with_scores)
        elif routed_with_scores == []:
            if not settings.RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK:
                scope_file_ids = []
            else:
                scope_file_ids = await _enumerate_scope_files(meeting_ids, user_id=user_id)
        else:
            routed_file_ids = await _route_scope_files_via_summary(
                primary_query,
                meeting_ids,
                trace=trace,
                user_id=user_id,
            )
            if routed_file_ids:
                scope_file_ids = routed_file_ids
            else:
                scope_file_ids = await _enumerate_scope_files(meeting_ids, user_id=user_id)

        # Anchor injection with cap+evict (consistent with funnel narrow)
        if settings.RAG_ANCHOR_BOOST_IN_BROAD_RECALL and anchor_file_ids:
            scope_file_ids, _ = apply_anchor_evict(
                scope_file_ids,
                anchor_file_ids,
                cap=target_files,
                quota_ratio=settings.RAG_ANCHOR_QUOTA_RATIO,
            )

        return ScopeSelection(
            scope_file_ids=scope_file_ids,
            file_scores=file_scores,
        )


_STRATEGY_MAP: dict[str, type] = {
    "router_and_funnel": RouterAndFunnelStrategy,
    "funnel_only": FunnelOnlyStrategy,
    "router_pre_filter": RouterPreFilterStrategy,
    "router_only": RouterOnlyStrategy,
}


def get_scoping_strategy(mode: str | None = None) -> FileScopingStrategy:
    """Return the file scoping strategy for the given mode."""
    selected_mode = mode or settings.RAG_FILE_SCOPING_MODE or "router_and_funnel"
    cls = _STRATEGY_MAP.get(selected_mode)
    if cls is None:
        raise ValueError(
            f"Unknown RAG_FILE_SCOPING_MODE={selected_mode!r}. Valid modes: {sorted(_STRATEGY_MAP)}"
        )
    return cls()
