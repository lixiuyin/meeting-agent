"""Shared dataclasses for the file-scoping pipeline.

Lives in its own module so that :mod:`_funnel_narrow` and
:mod:`_scoping_strategies` can both depend on it without forming a cycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScopeSelection:
    """Result of a file scoping strategy invocation.

    Attributes:
        scope_file_ids: ordered list of file IDs that survived selection.
        file_scores: per-file relevance scores in [0, 1].  Used by adaptive
            chunk allocation; not all entries in ``scope_file_ids`` need a
            score (anchor-only files default to 1.0 at the call site).
        docs_by_file: wide-fetch chunks grouped by ``file_id`` for reuse by
            :func:`fair_retrieve_per_file` (skips redundant Chroma calls
            when the same file is already represented in the cache).
            Empty for strategies that do not run a wide fetch.
    """

    scope_file_ids: list[int] = field(default_factory=list)
    file_scores: dict[int, float] = field(default_factory=dict)
    docs_by_file: dict[int, list[dict]] = field(default_factory=dict)


class BroadRecallContext:
    """Request-scoped memoization of wide-fetch results for multi-query variants.

    When multiple query variants share the same meeting scope, the expensive
    wide-fetch (Chroma vector search) runs once and the doc pool is shared.
    Each variant still runs its own router + funnel aggregation with its own
    query, so per-query ranking semantics are preserved on the rerank/aggregate
    steps — only the broad pool is reused.

    Uses ``asyncio.Future`` for coalescing so that different scope keys can
    compute in parallel while same-key waiters share a single result (H-4:
    previously held ``asyncio.Lock`` during ``await compute()``, serializing
    all keys).
    """

    def __init__(self) -> None:
        self._cache: dict[frozenset[int], list[dict]] = {}
        self._futures: dict[frozenset[int], asyncio.Future[list[dict]]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def make_key(
        meeting_ids: list[int] | None,
        anchor_meeting_ids: list[int] | None,
    ) -> frozenset[int]:
        """Compute a scope key from the effective meeting scope."""
        return frozenset(set(meeting_ids or []) | set(anchor_meeting_ids or []))

    async def get_or_compute(
        self,
        key: frozenset[int],
        compute: Callable[[], Awaitable[list[dict]]],
    ) -> list[dict]:
        """Return cached docs for *key*, or run *compute* and cache.

        Fast path returns immediately on cache hit.  Slow path uses a per-key
        ``asyncio.Future`` so concurrent requests for the same key coalesce
        onto a single compute, while requests for different keys run in
        parallel.
        """
        if key in self._cache:
            return self._cache[key]

        async with self._lock:
            if key in self._cache:
                return self._cache[key]
            future = self._futures.get(key)
            if future is None:
                future = asyncio.ensure_future(self._run_and_cache(key, compute))
                self._futures[key] = future

        return await future

    async def _run_and_cache(
        self,
        key: frozenset[int],
        compute: Callable[[], Awaitable[list[dict]]],
    ) -> list[dict]:
        try:
            docs = await compute()
            self._cache[key] = docs
            return docs
        finally:
            async with self._lock:
                self._futures.pop(key, None)
