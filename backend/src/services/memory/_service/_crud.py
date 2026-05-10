"""MemoryService CRUD mixin."""

import threading

from cachetools import LRUCache

from ....core import database as db
from ....core.audit import audit_log
from ....core.database import get_write_connection
from ....core.metrics import MEMORY_EVICT_TOTAL
from .._common import (
    _INITIAL_IMPORTANCE,
    _MAX_IMPORTANCE,
    _MAX_MEMORIES_PER_USER,
    _MIN_IMPORTANCE,
    logger,
)
from .._vectorstore import get_memory_vectorstore

# Per-key locks serialize concurrent set() calls for the same user+key
# to prevent interleaved Step 2/Step 3 that orphans vectors (HIGH-11).
# Capped with an LRU so multi-tenant deployments don't leak memory.
# Eviction is safe: the only invariant "serialises set() for this key"
# cannot break when no thread is holding the evicted lock.
_key_locks = LRUCache(maxsize=4096)
_key_locks_guard = threading.Lock()


def _get_key_lock(user_id: str, key: str) -> threading.Lock:
    """Return (creating if needed) a lock for serializing writes to a memory key."""
    k = (user_id, key)
    with _key_locks_guard:
        if k not in _key_locks:
            _key_locks[k] = threading.Lock()
        return _key_locks[k]


def _union_ids(a: list[int] | None, b: list[int] | None) -> list[int] | None:
    """Union two ID lists while preserving order and deduplicating."""
    if not a and not b:
        return None
    seen: set[int] = set()
    merged: list[int] = []
    for item in a or []:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    for item in b or []:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged or None


# H-MEM-2: Batch touch collector — defers write-lock acquisition from per-get
# to periodic flush, reducing write-lock contention from 20+ per session to 1.
_MAX_PENDING_TOUCHES = 5000
_pending_touches: dict[tuple[str, str], bool] = {}
_pending_touches_lock = threading.Lock()


def _queue_touch(user_id: str, key: str) -> None:
    """Queue a memory access touch for batch flush.

    When the pending dict grows beyond _MAX_PENDING_TOUCHES, the producer
    thread auto-flushes to prevent unbounded memory growth.
    """
    overflow = False
    with _pending_touches_lock:
        _pending_touches[(user_id, key)] = True
        if len(_pending_touches) >= _MAX_PENDING_TOUCHES:
            overflow = True
    if overflow:
        flush_pending_touches()


def _drain_pending_touches() -> dict[tuple[str, str], bool]:
    """Atomically drain and return pending touches. Caller must NOT hold the lock."""
    with _pending_touches_lock:
        if not _pending_touches:
            return {}
        pending = dict(_pending_touches)
        _pending_touches.clear()
        return pending


def flush_pending_touches() -> int:
    """Flush all pending memory access touches in a single write transaction.

    Called periodically (e.g. after pipeline steps complete). Returns count
    of flushed touches.
    """
    pending = _drain_pending_touches()
    if not pending:
        return 0

    # Group by user_id for efficient batch UPDATE
    by_user: dict[str, list[str]] = {}
    for uid, key in pending:
        by_user.setdefault(uid, []).append(key)

    flushed = 0
    for user_id, keys in by_user.items():
        try:
            with get_write_connection() as conn:
                placeholders = ",".join("?" for _ in keys)
                conn.execute(
                    f"UPDATE user_memories SET "
                    f"access_count = access_count + 1, "
                    f"last_accessed = CURRENT_TIMESTAMP "
                    f"WHERE user_id = ? AND key IN ({placeholders})",
                    [user_id, *keys],
                )
            flushed += len(keys)
        except Exception:
            logger.debug(
                "Batch touch flush failed for user %s (%d keys)",
                user_id,
                len(keys),
                exc_info=True,
            )
    return flushed


class _MemoryCrudMixin:
    def boost_recalled_entries(self, user_id: str, entries: list, boost: int = 1) -> None:
        """Boost importance using pre-fetched MemoryEntry data (M-MEM-3).

        Avoids redundant SQL re-reads by using value/category/scope data already
        available from the search phase.  Only the atomic importance increment
        needs a database round-trip.
        """
        if not entries:
            return
        for entry in entries:
            try:
                with get_write_connection() as conn:
                    cursor = conn.execute(
                        "UPDATE user_memories "
                        "SET importance = MIN(importance + ?, ?) "
                        "WHERE user_id = ? AND key = ? "
                        "RETURNING importance",
                        (boost, _MAX_IMPORTANCE, user_id, entry.key),
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue
                new_importance = row["importance"]
                try:
                    vs = get_memory_vectorstore()
                    # Metadata-only update — the document text and embedding
                    # are unchanged when only the importance score is bumped.
                    vs.bump_importance(
                        user_id,
                        entry.key,
                        new_importance,
                        category=entry.category,
                        meeting_ids=entry.meeting_ids,
                        file_ids=entry.file_ids,
                    )
                except Exception:
                    logger.warning(
                        "Failed to bump memory importance for key %s",
                        entry.key,
                        exc_info=True,
                    )
                audit_log(
                    "memory.boost",
                    "memory",
                    entry.key,
                    user_id=user_id,
                    detail=f"boost={boost}",
                )
            except Exception:
                logger.warning("Failed to boost memory %s", entry.key, exc_info=True)

    def boost_recalled(self, user_id: str, keys: list[str], boost: int = 1) -> None:
        """Increase importance of memories that were successfully recalled.

        Uses atomic SQL increment to prevent lost-write anomalies under concurrency.
        """
        if not keys:
            return
        for key in keys:
            try:
                # Atomic increment — avoids read-modify-write race condition.
                # Scope IDs come from the junction table (memory_scopes), so
                # we read them inside the same write transaction after the
                # importance bump rather than via RETURNING on a CSV column.
                with get_write_connection() as conn:
                    cursor = conn.execute(
                        "UPDATE user_memories "
                        "SET importance = MIN(importance + ?, ?) "
                        "WHERE user_id = ? AND key = ? "
                        "RETURNING id, importance, value, category",
                        (boost, _MAX_IMPORTANCE, user_id, key),
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue
                    from ....core.database._scopes import get_scopes

                    meeting_ids, file_ids = get_scopes(conn, kind="memory", owner_id=int(row["id"]))
                new_importance = row["importance"]
                try:
                    vs = get_memory_vectorstore()
                    # Metadata-only update — see bump_importance() docstring.
                    vs.bump_importance(
                        user_id,
                        key,
                        new_importance,
                        category=row["category"],
                        meeting_ids=meeting_ids or None,
                        file_ids=file_ids or None,
                    )
                except Exception:
                    logger.warning(
                        "Failed to bump memory importance for key %s", key, exc_info=True
                    )
                audit_log("memory.boost", "memory", key, user_id=user_id, detail=f"boost={boost}")
            except Exception:
                logger.warning("Failed to boost memory %s", key, exc_info=True)

    def get(self, user_id: str, key: str) -> str | None:
        """Get a single memory value."""
        with db.get_connection() as conn:
            value = db.get_memory(conn, user_id=user_id, key=key)
        if value is not None:
            # Queue touch for batch flush instead of per-get write-lock
            # contention (H-MEM-2). The batch is flushed periodically by
            # the memory service or on the next set() call.
            _queue_touch(user_id, key)
        return value

    def set(
        self,
        user_id: str,
        key: str,
        value: str,
        source: str = "manual",
        importance: float = _INITIAL_IMPORTANCE,
        expires_at: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
        turn_index: int | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> None:
        """Store a memory with importance, TTL, category, and provenance.

        ``meeting_ids`` / ``file_ids`` tag the memory with its originating scope
        so scoped chat queries can filter out unrelated context. When omitted,
        the memory is treated as global / unscoped.
        """
        importance = max(_MIN_IMPORTANCE, min(_MAX_IMPORTANCE, importance))

        # HIGH-11: Serialize writes to the same key so two concurrent set()
        # calls cannot interleave their Step 2 (SQL) / Step 3 (vector)
        # sequences and leave orphaned vectors.
        key_lock = _get_key_lock(user_id, key)
        with key_lock:
            self._set_locked(
                user_id,
                key,
                value,
                source,
                importance,
                expires_at,
                category,
                session_id,
                turn_index,
                meeting_ids,
                file_ids,
            )

    def _set_locked(
        self,
        user_id: str,
        key: str,
        value: str,
        source: str,
        importance: float,
        expires_at: str | None,
        category: str | None,
        session_id: str | None,
        turn_index: int | None,
        meeting_ids: list[int] | None,
        file_ids: list[int] | None,
    ) -> None:
        """Internal: execute the actual set() logic under per-key lock.

        Uses a single atomic SQL statement (INSERT ... ON CONFLICT DO UPDATE)
        for the core upsert, then merges scope IDs in the same transaction.
        This eliminates the read-then-write race between concurrent workers.
        """
        with get_write_connection() as conn:
            # Single atomic upsert — the SQL layer handles INSERT vs UPDATE
            db.set_memory(
                conn,
                user_id=user_id,
                key=key,
                value=value,
                source=source,
                importance=importance,
                expires_at=expires_at,
                category=category,
                embedding_id=None,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
            )
            if session_id is not None:
                conn.execute(
                    "UPDATE user_memories SET session_id=?, turn_index=? WHERE user_id=? AND key=?",
                    (session_id, turn_index, user_id, key),
                )

        # Step 3: Vector upsert outside lock, then backfill embedding_id.
        # Mark vector_state='pending' with updated_at touched so the startup
        # reconciler can gauge staleness (M-MEM-1).
        try:
            with get_write_connection() as conn:
                conn.execute(
                    "UPDATE user_memories SET vector_state='pending', "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE user_id=? AND key=?",
                    (user_id, key),
                )
        except Exception:
            pass  # best-effort state mark; upsert will proceed regardless

        embedding_id = None
        for attempt in range(3):
            try:
                vs = get_memory_vectorstore()
                embedding_id = vs.upsert(
                    user_id,
                    key,
                    value,
                    importance,
                    category,
                    meeting_ids=meeting_ids,
                    file_ids=file_ids,
                )
                break
            except Exception as e:
                if attempt < 2:
                    logger.debug(
                        "Vector store upsert attempt %d failed, retrying: %s",
                        attempt + 1,
                        e,
                    )
                else:
                    logger.warning(
                        "Failed to index memory vector for key %s after 3 attempts",
                        key,
                        exc_info=True,
                    )
        if embedding_id is not None:
            try:
                with get_write_connection() as conn:
                    conn.execute(
                        "UPDATE user_memories SET embedding_id=?, vector_state='synced' "
                        "WHERE user_id=? AND key=?",
                        (embedding_id, user_id, key),
                    )
            except Exception:
                logger.warning("Failed to backfill embedding_id for %s", key, exc_info=True)
        else:
            # Vector upsert failed after 3 retries — mark for later reconciliation.
            try:
                with get_write_connection() as conn:
                    conn.execute(
                        "UPDATE user_memories SET vector_state='failed' WHERE user_id=? AND key=?",
                        (user_id, key),
                    )
            except Exception:
                pass  # best-effort failure marking; reconciliation will catch it later

        self._enforce_memory_cap(user_id)
        audit_log(
            "memory.set",
            "memory",
            key,
            user_id=user_id,
            detail=f"source={source} importance={importance}",
        )

    def timeline(self, user_id: str, key: str, max_depth: int = 20) -> list[dict]:
        """Return the supersede chain for a memory, oldest → newest.

        Useful for answering historical questions ("what did the user use
        before?") without polluting active recall. Includes rows marked
        superseded that are otherwise filtered out of normal queries.
        """
        with db.get_connection() as conn:
            return db.get_memory_timeline(conn, user_id=user_id, key=key, max_depth=max_depth)

    def delete(self, user_id: str, key: str) -> None:
        """Delete a memory from both stores.

        If the vector store deletion fails, the embedding_id is recorded
        in ``pending_vector_deletions`` for cleanup on the next startup.
        """
        embedding_id: str | None = None
        with get_write_connection() as conn:
            embedding_id = db.delete_memory(conn, user_id=user_id, key=key)

        if embedding_id:
            try:
                vs = get_memory_vectorstore()
                vs.delete(embedding_id)
            except Exception as e:
                logger.warning(
                    "Failed to delete memory vector %s — queued for deferred cleanup: %s",
                    embedding_id,
                    e,
                    exc_info=True,
                )
                self._queue_pending_vector_deletion("memory", embedding_id)
        audit_log("memory.delete", "memory", key, user_id=user_id)

    @staticmethod
    def _queue_pending_vector_deletion(collection: str, embedding_id: str) -> None:
        """Record a failed vector deletion for later cleanup."""
        try:
            with get_write_connection() as conn:
                conn.execute(
                    "INSERT INTO pending_vector_deletions (collection, embedding_id) VALUES (?, ?)",
                    (collection, embedding_id),
                )
        except Exception:
            logger.warning(
                "Failed to queue pending vector deletion for %s/%s",
                collection,
                embedding_id,
                exc_info=True,
            )

    def _enforce_memory_cap(self, user_id: str) -> None:
        """Prune lowest-importance memories when per-user cap is exceeded."""
        # C-6: Count + select + delete in same write transaction to prevent TOCTOU race.
        to_prune: list[dict] = []
        with get_write_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM user_memories WHERE user_id=?",
                (user_id,),
            ).fetchone()["cnt"]
            if count <= _MAX_MEMORIES_PER_USER:
                return
            excess = count - _MAX_MEMORIES_PER_USER
            to_prune = conn.execute(
                """SELECT key, embedding_id FROM user_memories
                   WHERE user_id=? ORDER BY
                   (importance + EXP(
                       -(JULIANDAY('now') - JULIANDAY(
                           COALESCE(created_at, '2000-01-01'))) / 30.0
                   )) ASC
                   LIMIT ?""",
                (user_id, excess),
            ).fetchall()

            if to_prune:
                keys = [r["key"] for r in to_prune]
                placeholders = ",".join("?" * len(keys))
                conn.execute(
                    f"DELETE FROM user_memories WHERE user_id=? AND key IN ({placeholders})",
                    [user_id, *keys],
                )

        if not to_prune:
            return

        # Vector delete outside the write transaction (best-effort, non-blocking)
        embedding_ids = [r["embedding_id"] for r in to_prune if r.get("embedding_id")]
        if embedding_ids:
            try:
                vs = get_memory_vectorstore()
                for eid in embedding_ids:
                    vs.delete(eid)
            except Exception:
                logger.warning(
                    "Failed to batch-delete memory vectors for user %s",
                    user_id,
                    exc_info=True,
                )

        logger.info(
            "Pruned %d memories for user %s (cap: %d)",
            len(to_prune),
            user_id,
            _MAX_MEMORIES_PER_USER,
        )
        MEMORY_EVICT_TOTAL.inc(len(to_prune))

    def list_all(
        self,
        user_id: str = "default",
        include_expired: bool = False,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """List all memories for a user, optionally filtered."""
        with db.get_connection() as conn:
            return db.list_memories(
                conn,
                user_id=user_id,
                include_expired=include_expired,
                category=category,
                limit=limit,
                offset=offset,
            )


def cleanup_pending_vector_deletions() -> int:
    """Process any deferred vector deletions that failed previously.

    Called during startup.  Returns the number of vectors successfully deleted.
    Rows that fail more than ``settings.VECTOR_DELETION_MAX_ATTEMPTS`` times
    are moved to dead-letter status and removed from the retry queue.
    """
    from ....core.config import settings

    max_attempts = settings.VECTOR_DELETION_MAX_ATTEMPTS

    with get_write_connection() as conn:
        rows = conn.execute(
            "SELECT id, collection, embedding_id, "
            "COALESCE(attempts, 0) AS attempts "
            "FROM pending_vector_deletions"
        ).fetchall()

    if not rows:
        return 0

    cleaned = 0
    dead_ids: list[int] = []
    bumped_ids: list[int] = []
    remaining_ids: list[int] = []
    for row in rows:
        row_id = row["id"]
        attempts = row["attempts"]
        collection = row["collection"]
        embedding_id = row["embedding_id"]

        # Give up on permanently stuck entries
        if attempts >= max_attempts:
            logger.warning(
                "Giving up on pending vector deletion %s/%s after %d attempts",
                collection,
                embedding_id,
                attempts,
            )
            dead_ids.append(row_id)
            continue

        try:
            if collection == "memory":
                vs = get_memory_vectorstore()
                vs.delete(embedding_id)
            elif collection == "entity":
                from ...knowledge_graph._vectorstore import get_entity_vectorstore

                vs = get_entity_vectorstore()
                vs.delete(embedding_id)
            elif collection == "meeting":
                from ...rag import delete_meeting_chunks

                delete_meeting_chunks(int(embedding_id))
            elif collection == "raganything":
                from ...rag._indexer_store import _remove_from_raganything

                _remove_from_raganything(
                    meeting_id=0,
                    file_id=None,
                )
                try:
                    from ...rag._raganything import _get_raganything, _run_async

                    rag = _get_raganything()
                    eid = embedding_id

                    async def _del(ra=rag, doc_id=eid):
                        await ra.lightrag.adelete_by_doc_id(doc_id)

                    _run_async(_del())
                except Exception:
                    logger.debug(
                        "RAGAnything reconciler delete failed for %s",
                        eid,
                        exc_info=True,
                    )
            else:
                logger.warning("Unknown collection in pending deletions: %s", collection)
                remaining_ids.append(row_id)
                continue
            cleaned += 1
        except Exception as exc:
            type_name = type(exc).__name__
            if type_name == "NotFoundError":
                logger.debug(
                    "Deferred vector %s/%s already removed — marking cleaned",
                    collection,
                    embedding_id,
                )
                cleaned += 1
                continue
            logger.debug(
                "Deferred vector deletion still failing for %s/%s (attempt %d)",
                collection,
                embedding_id,
                attempts + 1,
                exc_info=True,
            )
            bumped_ids.append(row_id)
            remaining_ids.append(row_id)

    # Single write transaction: remove processed + dead-letter rows, bump attempts
    to_delete = [row["id"] for row in rows if row["id"] not in remaining_ids]
    to_delete.extend(dead_ids)
    if to_delete or bumped_ids:
        with get_write_connection() as conn:
            if to_delete:
                placeholders = ",".join("?" for _ in to_delete)
                conn.execute(
                    f"DELETE FROM pending_vector_deletions WHERE id IN ({placeholders})",
                    to_delete,
                )
            if bumped_ids:
                placeholders = ",".join("?" for _ in bumped_ids)
                conn.execute(
                    "UPDATE pending_vector_deletions "
                    f"SET attempts = attempts + 1 WHERE id IN ({placeholders})",
                    bumped_ids,
                )

    if cleaned:
        logger.info("Cleaned up %d pending vector deletions", cleaned)
        from ....core.metrics import VECTORSTORE_ORPHAN_TOTAL

        VECTORSTORE_ORPHAN_TOTAL.labels(collection="all").inc(cleaned)

    if dead_ids:
        logger.warning(
            "Removed %d permanently stuck vector deletions (exceeded %d attempts)",
            len(dead_ids),
            max_attempts,
        )
        from ....core.metrics import PENDING_VECTOR_DELETIONS_DEAD_LETTER_TOTAL

        PENDING_VECTOR_DELETIONS_DEAD_LETTER_TOTAL.inc(len(dead_ids))

    return cleaned


def requeue_pending_memory_vectors() -> int:
    """Re-queue memory vectors stuck in vector_state='pending' after a crash.

    Scans for rows that have been in 'pending' for longer than 5 minutes
    and re-triggers the vector upsert pipeline. Called during startup.

    Rows stuck >30 minutes are hard-failed to prevent indefinite retry (M-MEM-1).

    Returns count of re-queued vectors.
    """
    # M-MEM-1: Hard-fail rows stuck for >30 minutes.
    _hard_fail_sql = (
        "UPDATE user_memories SET vector_state = 'failed' "
        "WHERE vector_state = 'pending' "
        "AND updated_at < datetime('now', '-30 minutes')"
    )
    with get_write_connection() as conn:
        cursor = conn.execute(_hard_fail_sql)
        hard_failed = cursor.rowcount
    if hard_failed:
        logger.warning("Hard-failed %d memory vectors stuck in pending >30 minutes", hard_failed)

    cutoff_sql = (
        "SELECT id, user_id, key, value, importance, category "
        "FROM user_memories "
        "WHERE vector_state = 'pending' "
        "AND updated_at < datetime('now', '-5 minutes')"
    )
    with db.get_connection() as conn:
        rows = conn.execute(cutoff_sql).fetchall()

    if not rows:
        return 0

    requeued = 0
    for row in rows:
        user_id = row["user_id"]
        key = row["key"]
        try:
            vs = get_memory_vectorstore()
            embedding_id = vs.upsert(
                user_id,
                key,
                row["value"],
                row["importance"],
                row["category"],
            )
            if embedding_id is not None:
                with get_write_connection() as conn:
                    conn.execute(
                        "UPDATE user_memories SET embedding_id=?, vector_state='synced' "
                        "WHERE user_id=? AND key=?",
                        (embedding_id, user_id, key),
                    )
                requeued += 1
            else:
                with get_write_connection() as conn:
                    conn.execute(
                        "UPDATE user_memories SET vector_state='failed' WHERE user_id=? AND key=?",
                        (user_id, key),
                    )
        except Exception:
            logger.warning(
                "Failed to requeue pending memory vector for %s/%s",
                user_id,
                key,
                exc_info=True,
            )
            with get_write_connection() as conn:
                conn.execute(
                    "UPDATE user_memories SET vector_state='failed' WHERE user_id=? AND key=?",
                    (user_id, key),
                )

    if requeued:
        logger.info("Re-queued %d pending memory vectors", requeued)
    return requeued
