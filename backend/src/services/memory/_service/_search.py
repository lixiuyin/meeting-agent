"""MemoryService search mixin (important + semantic)."""

import asyncio
import calendar
import math
import time

from ....core import database as db
from ....core.config import settings
from ....core.metrics import MEMORY_SEARCH_TOTAL
from .._decay import _compute_decay_score
from .._entry import MemoryEntry
from .._vectorstore import get_memory_vectorstore

_ACCESS_COUNT_DECAY_RATE = 0.02  # half-life ~35 days for access_count contribution
_MAX_ACCESS_COUNT_FOR_SCORING = 100


def _normalize_importance(importance: float) -> float:
    return max(0.0, min(1.0, (float(importance) - 1.0) / 4.0))


def _normalize_access_score(access_count: int, access_decay: float) -> float:
    capped_count = max(0, min(access_count, _MAX_ACCESS_COUNT_FOR_SCORING))
    max_access = math.log1p(_MAX_ACCESS_COUNT_FOR_SCORING)
    return max(0.0, min(1.0, access_decay * math.log1p(capped_count) / max_access))


def _normalize_vector_score(raw: float) -> float:
    """Normalize a raw vector-store distance/similarity score to [0, 1].

    - L2 distance: lower is better → ``1 / (1 + d)`` maps [0, ∞) to (0, 1].
      Capped at a configurable max distance so cross-encoder scores (which
      can reach 30+) don't squash all results near zero (M-14).
    - Cosine similarity: higher is better, range [-1, 1] → ``(s + 1) / 2``
      maps to [0, 1].
    - Inner product: unnormalized; treat like cosine for now.
    """
    metric = str(settings.DISTANCE_METRIC).lower()
    if metric in ("cosine", "ip"):
        return max(0.0, min(1.0, (raw + 1.0) / 2.0))
    # Default: L2 or unknown — assume lower-is-better distance.
    max_d = getattr(settings, "VECTOR_MAX_DISTANCE", 20.0)
    return max(0.0, 1.0 - raw / max(1.0, max_d))


def _entry_matches_scope(
    entry_meeting_ids: list[int] | None,
    entry_file_ids: list[int] | None,
    filter_meeting_ids: list[int] | None,
    filter_file_ids: list[int] | None,
) -> bool:
    """Check if a memory entry matches the (meeting, file) scope filter.

    Returns True when the entry's scope intersects the filter's scope.
    Returns True when the entry has no scope (global) — caller decides
    whether to keep it based on a separate strict/cap policy.
    """
    # Global entry (no scope metadata) → return True; caller applies cap/strict
    if not entry_meeting_ids and not entry_file_ids:
        return True
    meeting_filter = set(filter_meeting_ids or [])
    file_filter = set(filter_file_ids or [])
    if meeting_filter and set(entry_meeting_ids or []) & meeting_filter:
        return True
    return bool(file_filter and set(entry_file_ids or []) & file_filter)


# Kept for backwards compat with any external callers
def _entry_matches_meeting_scope(
    entry_meeting_ids: list[int] | None,
    filter_meeting_ids: list[int],
) -> bool:
    return _entry_matches_scope(entry_meeting_ids, None, filter_meeting_ids, None)


class _MemorySearchMixin:
    SEMANTIC_WEIGHT: float
    DECAY_WEIGHT: float
    IMPORTANCE_WEIGHT: float

    def search_important(
        self,
        user_id: str,
        min_importance: float = 3,
        limit: int = 10,
    ) -> list[dict]:
        """Get important memories by importance score with decay applied.

        Read-only — touch writes are deferred to the caller (background task).
        """
        with db.get_connection() as conn:
            memories = db.search_memories_by_importance(
                conn,
                user_id=user_id,
                min_importance=min_importance,
                limit=limit * 2,
            )

        scored = []
        for m in memories:
            decay_score = _compute_decay_score(m.get("importance", 3), m.get("last_accessed"))
            # Time-weight the access_count so old heavily-accessed memories
            # don't permanently dominate newer high-importance ones.
            access_count = m.get("access_count", 0)
            last_accessed = m.get("last_accessed")
            access_decay = 1.0
            if last_accessed and access_count > 0:
                try:
                    last_ts = calendar.timegm(time.strptime(last_accessed, "%Y-%m-%d %H:%M:%S"))
                    days_since = (time.time() - last_ts) / 86400
                    access_decay = math.exp(-_ACCESS_COUNT_DECAY_RATE * days_since)
                except (ValueError, TypeError):
                    pass
            # Weights map to their namesake components: decay_weight →
            # decay_score, importance_weight → normalized importance,
            # semantic_weight → access_score (as a relevance-activity proxy).
            combined = (
                self.DECAY_WEIGHT * decay_score
                + self.IMPORTANCE_WEIGHT * _normalize_importance(m.get("importance", 3))
                + self.SEMANTIC_WEIGHT * _normalize_access_score(access_count, access_decay)
            )
            scored.append((combined, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]

    async def search_semantic(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        min_importance: float = 1,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> list[MemoryEntry]:
        """Semantic search over memories using vector similarity.

        When ``meeting_ids`` or ``file_ids`` is provided, results are
        post-filtered:
        - Entries whose ``meeting_ids`` or ``file_ids`` intersect the filter
          are kept.
        - Entries with no scope metadata (global/unscoped) are kept, subject
          to one of two policies:
            * default: capped at ``settings.GLOBAL_MEMORY_LIMIT``, except
              ``user_profile`` category entries which are always kept.
        """
        try:
            return await self._search_semantic_impl(
                user_id, query, limit, min_importance, meeting_ids, file_ids
            )
        except Exception:
            MEMORY_SEARCH_TOTAL.labels(status="error").inc()
            raise

    async def _search_semantic_impl(
        self,
        user_id: str,
        query: str,
        limit: int,
        min_importance: float,
        meeting_ids: list[int] | None,
        file_ids: list[int] | None,
    ) -> list[MemoryEntry]:
        from ....core.config import settings as core_settings

        vs = get_memory_vectorstore()
        # When a scope is active the post-filter may discard many of the
        # top-K results; over-fetch so that enough scope-matching entries
        # remain to fill the result limit.
        scope_active = bool(meeting_ids or file_ids)
        oversample = (
            getattr(core_settings, "MEMORY_SEARCH_OVERSAMPLE_FACTOR", 5) if scope_active else 2
        )
        results = await asyncio.to_thread(
            vs.similarity_search,
            query,
            user_id,
            limit,
            min_importance,
            fetch_multiplier=oversample,
        )

        # Batch-fetch full records in a single SELECT (N+1 → 1 query)
        keys = [r["key"] for r in results]
        with db.get_connection() as conn:
            batch = await asyncio.to_thread(db.get_memories_batch, conn, user_id=user_id, keys=keys)

        entries: list[MemoryEntry] = []
        keys_found: set[str] = set()
        for r in results:
            key = r["key"]
            keys_found.add(key)
            full = batch.get(key)
            if not full:
                continue
            entry = MemoryEntry(
                key=key,
                value=full.get("value", ""),
                importance=full.get("importance", 3),
                category=full.get("category") or r.get("category"),
                source=full.get("source", "unknown"),
                last_accessed=full.get("last_accessed"),
                access_count=full.get("access_count", 0),
                expires_at=full.get("expires_at"),
                updated_at=full.get("updated_at", ""),
                meeting_ids=r.get("meeting_ids"),
                file_ids=r.get("file_ids"),
                is_legacy_scope=bool(full.get("is_legacy_scope", 0)),
                decay_score=_compute_decay_score(
                    full.get("importance", 3), full.get("last_accessed")
                ),
                semantic_score=_normalize_vector_score(r["score"]),
                combined_score=0.0,
            )
            entry.combined_score = (
                self.SEMANTIC_WEIGHT * entry.semantic_score
                + self.DECAY_WEIGHT * _normalize_importance(entry.decay_score)
                + self.IMPORTANCE_WEIGHT * _normalize_importance(entry.importance)
            )
            entries.append(entry)

        important = self.search_important(user_id, min_importance=min_importance, limit=limit)
        for m in important:
            if m["key"] in keys_found:
                continue
            keys_found.add(m["key"])
            entry = MemoryEntry(
                key=m["key"],
                value=m.get("value", ""),
                importance=m.get("importance", 3),
                category=m.get("category"),
                source=m.get("source", "unknown"),
                last_accessed=m.get("last_accessed"),
                access_count=m.get("access_count", 0),
                expires_at=m.get("expires_at"),
                updated_at=m.get("updated_at", ""),
                meeting_ids=None,  # important-sourced entries lack vector metadata
                is_legacy_scope=bool(m.get("is_legacy_scope", 0)),
                decay_score=_compute_decay_score(m.get("importance", 3), m.get("last_accessed")),
                semantic_score=0.0,
                combined_score=0.0,
            )
            entry.combined_score = (
                self.SEMANTIC_WEIGHT * entry.semantic_score
                + self.DECAY_WEIGHT * _normalize_importance(entry.decay_score)
                + self.IMPORTANCE_WEIGHT * _normalize_importance(entry.importance)
            )
            entries.append(entry)

        entries.sort(key=lambda x: x.combined_score, reverse=True)

        # Apply scope filtering when a meeting or file selection is active.
        # Without a selection, all entries are considered global and returned.
        if meeting_ids or file_ids:
            from ....core.config import settings

            strict = getattr(settings, "SCOPED_MEMORY_STRICT", False)
            global_limit = settings.GLOBAL_MEMORY_LIMIT
            scoped: list[MemoryEntry] = []
            global_count = 0
            for entry in entries:
                is_global = not entry.meeting_ids and not entry.file_ids
                if is_global:
                    is_profile = (entry.category or "").lower() == "user_profile"
                    # User-profile memories always pass; they are user-level,
                    # not meeting-level, and should persist across scopes.
                    if is_profile:
                        scoped.append(entry)
                        continue
                    # Legacy pre-scope memories are treated as unknown-scope
                    # and excluded from scoped queries entirely — they remain
                    # visible only in unscoped queries.
                    if entry.is_legacy_scope:
                        continue
                    if strict:
                        continue
                    if global_count >= global_limit:
                        continue
                    global_count += 1
                    scoped.append(entry)
                    continue
                if _entry_matches_scope(entry.meeting_ids, entry.file_ids, meeting_ids, file_ids):
                    scoped.append(entry)
            return scoped[:limit]

        MEMORY_SEARCH_TOTAL.labels(status="success").inc()
        return entries[:limit]
