"""MemoryService search mixin (important + semantic)."""

import asyncio

from ....core import database as db
from ....core.memory_admission import is_reference_memory
from ....core.memory_policy import is_active_memory
from ....core.memory_query import ActionConstraints, memory_scope_matches
from ....core.metrics import MEMORY_SEARCH_TOTAL
from .._decay import _compute_decay_score
from .._entry import MemoryEntry
from .._parsers import _multilingual_tokens
from .._vectorstore import get_memory_vectorstore

_MULTI_HOP_CONTENT_LIMIT = 300
_MULTI_HOP_FETCH_MULTIPLIER = 4


def _normalize_importance(importance: float) -> float:
    return max(0.0, min(1.0, (float(importance) - 1.0) / 4.0))


def _freshness_for_row(memory: dict) -> float:
    """Compute freshness from fact confirmation time without mutating salience."""
    reference = (
        memory.get("last_confirmed_at") or memory.get("updated_at") or memory.get("created_at")
    )
    return max(
        0.0,
        min(
            1.0,
            float(
                _compute_decay_score(
                    1,
                    reference,
                    expires_at=memory.get("valid_to") or memory.get("expires_at"),
                )
            ),
        ),
    )


def _weighted_average(*components: tuple[float, float]) -> float:
    total = sum(max(0.0, weight) for weight, _value in components)
    if total <= 0:
        return 0.0
    return sum(max(0.0, weight) * max(0.0, min(1.0, value)) for weight, value in components) / total


def _normalize_vector_score(raw: float) -> float:
    """Normalize a raw vector-store distance/similarity score to [0, 1].

    LangChain Chroma's ``similarity_search_with_score`` returns a distance for
    every supported collection metric, including cosine.  Lower is therefore
    always better at this adapter boundary.
    """
    return 1.0 / (1.0 + max(float(raw), 0.0))


def _decode_scope_ids(raw: object) -> list[int] | None:
    """Normalize DB CSV scope values and vector-store lists to one type."""
    if raw is None:
        return None
    values = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if parsed not in seen:
            seen.add(parsed)
            result.append(parsed)
    return result or None


def _build_multi_hop_query(query: str, results: list[dict], seed_count: int) -> str | None:
    """Expand a query with bounded, lexically grounded first-hop facts.

    Vector top-k can be crowded by semantically generic high-salience facts.
    Reordering only the already-retrieved candidates by multilingual token
    overlap gives the graph-expansion step useful bridge facts without
    replacing semantic retrieval or scanning the whole memory store.
    """
    query_tokens = _multilingual_tokens(query)

    def _seed_score(result: dict) -> tuple[float, float]:
        content_tokens = _multilingual_tokens(str(result.get("content", "")))
        overlap = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
        # Chroma exposes distance at this boundary, so smaller is the
        # deterministic tie-breaker after lexical grounding.
        return overlap, -float(result.get("score", 0.0))

    ranked = [
        result
        for result in sorted(results, key=_seed_score, reverse=True)
        if _seed_score(result)[0] > 0
    ]
    facts = [
        str(result.get("content", ""))[:_MULTI_HOP_CONTENT_LIMIT]
        for result in ranked[: max(seed_count, 0)]
        if str(result.get("content", "")).strip()
    ]
    if not facts:
        return None
    return query + "\nRelated memory facts:\n" + "\n".join(facts)


def _bridge_scores(expanded_query: str | None, results: list[dict]) -> dict[str, float]:
    """Score bounded second-hop candidates against the grounded expansion."""
    if not expanded_query:
        return {}
    query_tokens = _multilingual_tokens(expanded_query)
    scores: dict[str, float] = {}
    for result in results:
        key = str(result.get("key", ""))
        content_tokens = _multilingual_tokens(str(result.get("content", "")))
        if not key or not content_tokens:
            continue
        overlap = len(query_tokens & content_tokens) / min(len(query_tokens), len(content_tokens))
        if overlap > 0:
            scores[key] = overlap
    return scores


def _select_with_bridge_reserve(
    entries: list[MemoryEntry],
    *,
    bridge_scores: dict[str, float],
    limit: int,
    reserve_count: int,
) -> list[MemoryEntry]:
    """Keep a small bridge quota while respecting the hard result limit."""
    if not bridge_scores or reserve_count <= 0:
        return entries[:limit]
    reserved = sorted(
        (entry for entry in entries if entry.key in bridge_scores),
        key=lambda entry: (bridge_scores[entry.key], entry.combined_score),
        reverse=True,
    )[: min(reserve_count, limit)]
    reserved_keys = {entry.key for entry in reserved}
    selected = [*reserved]
    selected.extend(entry for entry in entries if entry.key not in reserved_keys)
    return selected[:limit]


def _merge_vector_results(*groups: list[dict]) -> list[dict]:
    """Deduplicate first/second-hop results, preserving the best similarity."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for group in groups:
        for result in group:
            key = str(result.get("key", ""))
            if not key:
                continue
            if key not in merged:
                merged[key] = result
                order.append(key)
                continue
            if _normalize_vector_score(float(result.get("score", 0.0))) > _normalize_vector_score(
                float(merged[key].get("score", 0.0))
            ):
                merged[key] = result
    return [merged[key] for key in order]


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
    return memory_scope_matches(
        set(entry_meeting_ids or []), set(entry_file_ids or []), meeting_filter, file_filter
    )


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
    CONFIDENCE_WEIGHT: float
    USEFULNESS_WEIGHT: float

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
            if not is_active_memory(m):
                continue
            freshness = _freshness_for_row(m)
            combined = _weighted_average(
                (self.DECAY_WEIGHT, freshness),
                (self.IMPORTANCE_WEIGHT, _normalize_importance(m.get("salience", 3))),
                (self.CONFIDENCE_WEIGHT, float(m.get("confidence", 1.0))),
                (self.USEFULNESS_WEIGHT, float(m.get("usefulness_score", 0.0))),
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
        exclude_reference: bool = False,
        project_ids: tuple[str, ...] = (),
        action_constraints: ActionConstraints | None = None,
        filters=None,
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
                user_id,
                query,
                limit,
                min_importance,
                meeting_ids,
                file_ids,
                exclude_reference,
                project_ids,
                action_constraints,
                filters,
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
        exclude_reference: bool = False,
        project_ids: tuple[str, ...] = (),
        action_constraints: ActionConstraints | None = None,
        filters=None,
    ) -> list[MemoryEntry]:
        from ....core.config import settings as core_settings

        vs = await asyncio.to_thread(get_memory_vectorstore)
        # When a scope is active the post-filter may discard many of the
        # top-K results; over-fetch so that enough scope-matching entries
        # remain to fill the result limit.
        scope_active = bool(meeting_ids or file_ids)
        oversample = (
            getattr(core_settings, "MEMORY_SEARCH_OVERSAMPLE_FACTOR", 5) if scope_active else 2
        )
        allowed_keys: list[str] | None = None
        if scope_active or exclude_reference or project_ids or action_constraints or filters:
            with db.get_connection() as conn:
                allowed_keys = db.list_memory_keys_for_scope(
                    conn,
                    user_id=user_id,
                    meeting_ids=meeting_ids,
                    file_ids=file_ids,
                    include_unscoped=not core_settings.SCOPED_MEMORY_STRICT,
                    project_ids=project_ids,
                    exclude_reference=exclude_reference,
                    action_constraints=action_constraints,
                    **({"filters": filters} if filters is not None else {}),
                )
        if allowed_keys == []:
            return []
        candidate_k = max(limit * oversample, limit)
        results = await asyncio.to_thread(
            vs.similarity_search,
            query,
            user_id,
            candidate_k,
            min_importance,
            fetch_multiplier=oversample,
            allowed_keys=allowed_keys,
        )

        def _fetch_valid(candidates: list[dict]) -> tuple[list[dict], dict]:
            with db.get_connection() as conn:
                rows = db.get_memories_batch(
                    conn, user_id=user_id, keys=[r["key"] for r in candidates]
                )
            valid = []
            for result in candidates:
                row = rows.get(result["key"])
                if not row or not is_active_memory(row):
                    continue
                if filters is not None and not filters.matches(row, conn=conn):
                    continue
                if exclude_reference and is_reference_memory(row):
                    continue
                if project_ids and row.get("project_id") not in project_ids:
                    continue
                if (
                    action_constraints
                    and row.get("fact_type") == "action_item"
                    and not action_constraints.matches(row.get("action_status"))
                ):
                    continue
                expected = f"{row.get('id')}:{row.get('revision', 1)}"
                if result.get("generation") is not None:
                    if result["generation"] != expected:
                        continue
                elif row.get("revision", 1) > 1:
                    continue  # Legacy vector for a subsequently edited fact.
                if float(row.get("importance", 3)) < min_importance:
                    continue
                mids = _decode_scope_ids(row.get("meeting_ids", result.get("meeting_ids")))
                fids = _decode_scope_ids(row.get("file_ids", result.get("file_ids")))
                if scope_active:
                    if mids or fids:
                        if not _entry_matches_scope(mids, fids, meeting_ids, file_ids):
                            continue
                    elif (row.get("category") or "").lower() not in {
                        "profile",
                        "user_profile",
                    } and (row.get("is_legacy_scope") or core_settings.SCOPED_MEMORY_STRICT):
                        continue
                # Expansion uses the current SQL fact, never stale vector text.
                valid.append(
                    {
                        **result,
                        "content": f"{result['key']}: {row.get('value', '')}",
                        "meeting_ids": mids,
                        "file_ids": fids,
                    }
                )
            return valid, rows

        results, batch = await asyncio.to_thread(_fetch_valid, results)
        expanded_query: str | None = None
        if core_settings.MEMORY_MULTI_HOP_ENABLED and len(results) >= 2:
            expanded_query = _build_multi_hop_query(
                query,
                results,
                core_settings.MEMORY_MULTI_HOP_SEED_COUNT,
            )
            if expanded_query is not None:
                second_hop = await asyncio.to_thread(
                    vs.similarity_search,
                    expanded_query,
                    user_id,
                    max(limit * _MULTI_HOP_FETCH_MULTIPLIER, limit),
                    min_importance,
                    fetch_multiplier=oversample,
                    allowed_keys=allowed_keys,
                )
                second_hop, second_batch = await asyncio.to_thread(_fetch_valid, second_hop)
                results = _merge_vector_results(results, second_hop)
                batch.update(second_batch)
        bridge_scores = _bridge_scores(expanded_query, results)

        entries: list[MemoryEntry] = []
        keys_found: set[str] = set()
        for r in results:
            key = r["key"]
            keys_found.add(key)
            full = batch.get(key)
            if not full:
                continue
            if float(full.get("importance", 3)) < min_importance:
                # Chroma metadata is a derived cache and may lag a decay/write.
                # Enforce the authoritative SQLite threshold as well.
                continue
            entry = MemoryEntry(
                key=key,
                value=full.get("value", ""),
                importance=full.get("importance", 3),
                salience=full.get("salience", full.get("importance", 3)),
                confidence=full.get("confidence", 1.0),
                freshness_score=_freshness_for_row(full),
                usefulness_score=full.get("usefulness_score", 0.0),
                usefulness_count=full.get("usefulness_count", 0),
                category=full.get("category") or r.get("category"),
                source=full.get("source", "unknown"),
                last_accessed=full.get("last_accessed"),
                access_count=full.get("access_count", 0),
                expires_at=full.get("expires_at"),
                updated_at=full.get("updated_at", ""),
                meeting_ids=_decode_scope_ids(full.get("meeting_ids", r.get("meeting_ids"))),
                file_ids=_decode_scope_ids(full.get("file_ids", r.get("file_ids"))),
                metadata=full,
                is_legacy_scope=bool(full.get("is_legacy_scope", 0)),
                decay_score=_freshness_for_row(full),
                semantic_score=_normalize_vector_score(r["score"]),
                combined_score=0.0,
            )
            entry.combined_score = _weighted_average(
                (self.SEMANTIC_WEIGHT, entry.semantic_score),
                (self.DECAY_WEIGHT, entry.freshness_score),
                (self.IMPORTANCE_WEIGHT, _normalize_importance(entry.salience)),
                (self.CONFIDENCE_WEIGHT, entry.confidence),
                (self.USEFULNESS_WEIGHT, entry.usefulness_score),
            )
            entries.append(entry)

        if allowed_keys is not None:

            def _load_scoped_fallback() -> list[dict]:
                scoped_rows: list[dict] = []
                with db.get_connection() as conn:
                    # The SQL allow-list is ordered by salience. Bound the
                    # deterministic fallback independently from vector recall.
                    fallback_keys = (allowed_keys or [])[:candidate_k]
                    for start in range(0, len(fallback_keys), 400):
                        keys = fallback_keys[start : start + 400]
                        batch_rows = db.get_memories_batch(conn, user_id=user_id, keys=keys)
                        from ....core.memory_admission import is_reference_memory

                        scoped_rows.extend(
                            {
                                **row,
                                "key": key,
                                "_reference_memory": is_reference_memory(row, conn=conn),
                            }
                            for key, row in batch_rows.items()
                            if key in keys
                        )
                return scoped_rows

            important = await asyncio.to_thread(_load_scoped_fallback)
        else:
            important = await asyncio.to_thread(
                self.search_important,
                user_id,
                min_importance=min_importance,
                limit=candidate_k,
            )
        for m in important:
            if not is_active_memory(m):
                continue
            if filters is not None and not filters.matches(m):
                continue
            if exclude_reference and is_reference_memory(m):
                continue
            if project_ids and m.get("project_id") not in project_ids:
                continue
            if float(m.get("importance", 3)) < min_importance:
                continue
            if (
                action_constraints
                and m.get("fact_type") == "action_item"
                and not action_constraints.matches(m.get("action_status"))
            ):
                continue
            if m["key"] in keys_found:
                continue
            keys_found.add(m["key"])
            entry = MemoryEntry(
                key=m["key"],
                value=m.get("value", ""),
                importance=m.get("importance", 3),
                salience=m.get("salience", m.get("importance", 3)),
                confidence=m.get("confidence", 1.0),
                freshness_score=_freshness_for_row(m),
                usefulness_score=m.get("usefulness_score", 0.0),
                usefulness_count=m.get("usefulness_count", 0),
                category=m.get("category"),
                source=m.get("source", "unknown"),
                last_accessed=m.get("last_accessed"),
                access_count=m.get("access_count", 0),
                expires_at=m.get("expires_at"),
                updated_at=m.get("updated_at", ""),
                meeting_ids=_decode_scope_ids(m.get("meeting_ids")),
                file_ids=_decode_scope_ids(m.get("file_ids")),
                is_legacy_scope=bool(m.get("is_legacy_scope", 0)),
                decay_score=_freshness_for_row(m),
                semantic_score=0.0,
                combined_score=0.0,
                metadata=m,
            )
            entry.combined_score = _weighted_average(
                (self.SEMANTIC_WEIGHT, 0.0),
                (self.DECAY_WEIGHT, entry.freshness_score),
                (self.IMPORTANCE_WEIGHT, _normalize_importance(entry.salience)),
                (self.CONFIDENCE_WEIGHT, entry.confidence),
                (self.USEFULNESS_WEIGHT, entry.usefulness_score),
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
                    is_profile = (entry.category or "").lower() in {"profile", "user_profile"}
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
            MEMORY_SEARCH_TOTAL.labels(status="success").inc()
            return _select_with_bridge_reserve(
                scoped,
                bridge_scores=bridge_scores,
                limit=limit,
                reserve_count=core_settings.MEMORY_MULTI_HOP_SEED_COUNT,
            )

        MEMORY_SEARCH_TOTAL.labels(status="success").inc()
        return _select_with_bridge_reserve(
            entries,
            bridge_scores=bridge_scores,
            limit=limit,
            reserve_count=core_settings.MEMORY_MULTI_HOP_SEED_COUNT,
        )
