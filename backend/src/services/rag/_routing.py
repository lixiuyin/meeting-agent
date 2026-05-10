"""Summary-router and scope enumeration helpers.

These functions answer the question "which files should we even consider
for retrieval?" before the funnel narrow stage runs.  They were previously
located in ``chain/_retrieve_routing.py`` but only depend on RAG-layer
primitives (the summary vectorstore, the database, and metrics), so they
live here to keep the dependency direction clean and avoid the circular
import that previously forced a lazy ``importlib`` shim.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ...core.config import settings
from ...core.database import (
    get_connection,
    list_ready_file_ids_for_meetings,
    list_recent_ready_file_ids,
)
from ...core.metrics import (
    SUMMARY_ROUTER_FILES_ROUTED,
    SUMMARY_ROUTER_REQUEST_TOTAL,
)
from ._summary_router import route_files_by_summary, route_files_with_scores

if TYPE_CHECKING:
    from ...core.trace import TraceContext

logger = logging.getLogger(__name__)


async def _enumerate_scope_files(meeting_ids: list[int] | None) -> list[int]:
    """Enumerate ready file IDs for broad recall mode."""
    limit = settings.BROAD_RECALL_MAX_FILES

    def _sync() -> list[int]:
        try:
            with get_connection() as conn:
                if meeting_ids:
                    return list_ready_file_ids_for_meetings(conn, meeting_ids)
                return list_recent_ready_file_ids(conn, limit=limit)
        except Exception:
            logger.warning("Failed to enumerate scope files", exc_info=True)
            return []

    return await asyncio.to_thread(_sync)


async def _route_scope_files_via_summary(
    query: str,
    meeting_ids: list[int] | None,
    *,
    trace: TraceContext | None,
) -> list[int] | None:
    """Use summary-vector router to pre-narrow files for broad recall.

    Returns:
      * a non-empty file_id list when routing succeeded,
      * an empty list when routing ran but matched nothing (caller may
        still fall back to legacy enumeration depending on config),
      * ``None`` when routing is disabled or unavailable -- caller falls
        back unconditionally.
    """
    if not settings.RAG_SUMMARY_ROUTER_ENABLED:
        SUMMARY_ROUTER_REQUEST_TOTAL.labels(result="disabled").inc()
        return None

    span = (
        trace.start_span(
            "summary_router",
            "retrieve",
            parent_label="retrieve",
            meeting_count=len(meeting_ids) if meeting_ids else 0,
        )
        if trace
        else None
    )
    try:
        routed = await asyncio.to_thread(
            route_files_by_summary,
            query,
            meeting_ids,
        )
    except Exception:
        logger.warning("Summary router invocation failed", exc_info=True)
        SUMMARY_ROUTER_REQUEST_TOTAL.labels(result="error").inc()
        if span:
            span.finish("error")
        return None

    if span:
        span.metadata["routed_count"] = 0 if routed is None else len(routed)
        span.finish("success")

    if routed is None:
        SUMMARY_ROUTER_REQUEST_TOTAL.labels(result="miss").inc()
    else:
        count = len(routed)
        SUMMARY_ROUTER_REQUEST_TOTAL.labels(result="hit" if count else "miss").inc()
        if count:
            SUMMARY_ROUTER_FILES_ROUTED.observe(count)

    return routed


async def _route_scope_files_with_scores(
    query: str,
    meeting_ids: list[int] | None,
    *,
    trace: TraceContext | None,
) -> list[tuple[int, float]] | None:
    """Like ``_route_scope_files_via_summary`` but returns scored pairs."""
    if not settings.RAG_SUMMARY_ROUTER_ENABLED:
        return None

    try:
        return await asyncio.to_thread(
            route_files_with_scores,
            query,
            meeting_ids,
        )
    except Exception:
        logger.warning("Summary router (scored) invocation failed", exc_info=True)
        return None


async def router_prefilter_meetings(
    query: str,
    meeting_ids: list[int] | None,
    *,
    trace: TraceContext | None,
) -> list[int] | None:
    """Use summary router to narrow meeting scope for funnel pre-filtering.

    Returns meeting IDs derived from the router's top file selections,
    intersected with any user-specified meeting scope.
    """
    routed_file_ids = await _route_scope_files_via_summary(
        query,
        meeting_ids,
        trace=trace,
    )
    if not routed_file_ids:
        return meeting_ids

    def _map_files_to_meetings(file_ids: list[int]) -> list[int] | None:
        try:
            with get_connection() as conn:
                placeholders = ",".join("?" for _ in file_ids)
                rows = conn.execute(
                    f"SELECT DISTINCT meeting_id FROM meeting_files WHERE id IN ({placeholders})",
                    file_ids,
                ).fetchall()
                return [r["meeting_id"] for r in rows] or None
        except Exception:
            logger.warning("Failed to map router files to meetings", exc_info=True)
            return None

    prefiltered = await asyncio.to_thread(_map_files_to_meetings, routed_file_ids)
    if not prefiltered:
        return meeting_ids
    return prefiltered
