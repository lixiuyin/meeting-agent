"""MemoryService CRUD mixin."""

import threading
import uuid
from contextlib import ExitStack
from typing import Any

from ....core import database as db
from ....core.audit import audit_log
from ....core.database import get_write_connection
from .._common import logger
from .._vectorstore import get_memory_vectorstore
from . import settings

# Fixed striped locks serialize every mutation of a user+key without an
# unbounded registry or unsafe eviction of a lock that is still held.
_KEY_LOCK_STRIPES = 4096
_key_locks = tuple(threading.RLock() for _ in range(_KEY_LOCK_STRIPES))


def _get_key_lock(user_id: str, key: str) -> Any:
    """Return a stable striped lock for serializing all writes to a memory key."""
    return _key_locks[hash((user_id, key)) % _KEY_LOCK_STRIPES]


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
                        "SET importance = MIN(importance + ?, ?), "
                        "salience = MIN(salience + ?, ?) "
                        "WHERE user_id = ? AND key = ? "
                        "RETURNING importance, embedding_id",
                        (
                            boost,
                            settings.MEMORY_MAX_IMPORTANCE,
                            boost,
                            settings.MEMORY_MAX_IMPORTANCE,
                            user_id,
                            entry.key,
                        ),
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue
                new_importance = row["importance"]
                try:
                    vs = get_memory_vectorstore()
                    # Metadata-only update — the document text and embedding
                    # are unchanged when only the importance score is bumped.
                    synced = vs.bump_importance(
                        user_id,
                        entry.key,
                        new_importance,
                        embedding_id=row["embedding_id"],
                        category=entry.category,
                        meeting_ids=entry.meeting_ids,
                        file_ids=entry.file_ids,
                    )
                    if not synced:
                        with get_write_connection() as conn:
                            conn.execute(
                                "UPDATE user_memories SET vector_state='pending' "
                                "WHERE user_id=? AND key=? AND embedding_id IS ?",
                                (user_id, entry.key, row["embedding_id"]),
                            )
                except Exception:
                    with get_write_connection() as conn:
                        conn.execute(
                            "UPDATE user_memories SET vector_state='pending' "
                            "WHERE user_id=? AND key=? AND embedding_id IS ?",
                            (user_id, entry.key, row["embedding_id"]),
                        )
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
                        "SET importance = MIN(importance + ?, ?), "
                        "salience = MIN(salience + ?, ?) "
                        "WHERE user_id = ? AND key = ? "
                        "RETURNING id, importance, value, category, embedding_id",
                        (
                            boost,
                            settings.MEMORY_MAX_IMPORTANCE,
                            boost,
                            settings.MEMORY_MAX_IMPORTANCE,
                            user_id,
                            key,
                        ),
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
                    synced = vs.bump_importance(
                        user_id,
                        key,
                        new_importance,
                        embedding_id=row["embedding_id"],
                        category=row["category"],
                        meeting_ids=meeting_ids or None,
                        file_ids=file_ids or None,
                    )
                    if not synced:
                        with get_write_connection() as conn:
                            conn.execute(
                                "UPDATE user_memories SET vector_state='pending' "
                                "WHERE user_id=? AND key=? AND embedding_id IS ?",
                                (user_id, key, row["embedding_id"]),
                            )
                except Exception:
                    with get_write_connection() as conn:
                        conn.execute(
                            "UPDATE user_memories SET vector_state='pending' "
                            "WHERE user_id=? AND key=? AND embedding_id IS ?",
                            (user_id, key, row["embedding_id"]),
                        )
                    logger.warning(
                        "Failed to bump memory importance for key %s", key, exc_info=True
                    )
                audit_log("memory.boost", "memory", key, user_id=user_id, detail=f"boost={boost}")
            except Exception:
                logger.warning("Failed to boost memory %s", key, exc_info=True)

    def get(
        self,
        user_id: str,
        key: str,
        *,
        excluded_session_ids: set[str] | None = None,
    ) -> str | None:
        """Get a single memory value."""
        from ....core.memory_policy import is_active_memory

        with db.get_connection() as conn, conn:
            if key == "__profile__":
                if not conn.in_transaction:
                    conn.execute("BEGIN")
                from ....core.database.memory_lifecycle import profile_sources, valid_profile
                from ..evidence_admission import admissible_memories

                profile = valid_profile(
                    conn,
                    user_id,
                    excluded_session_ids=excluded_session_ids,
                )
                if not profile:
                    return None
                sources = profile_sources(conn, user_id)
                return (
                    profile
                    if sources and len(admissible_memories(conn, sources, user_id)) == len(sources)
                    else None
                )
            row = db.get_memory_full(conn, user_id=user_id, key=key)
        value = row["value"] if row and is_active_memory(row) else None
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
        importance: float | None = None,
        expires_at: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
        turn_index: int | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        confidence: float | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        evidence_message_ids: list[int] | None = None,
        evidence_excerpt: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        conflicts_with: list[str] | None = None,
        supersedes: list[str] | None = None,
        fact_type: str = "fact",
        assertion_status: str = "confirmed",
        project_id: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        object_value: str | None = None,
        action_status: str | None = None,
        assignee: str | None = None,
        due_at: str | None = None,
        expected_revision: int | None = None,
    ) -> None:
        """Store a memory with importance, TTL, category, and provenance.

        ``meeting_ids`` / ``file_ids`` tag the memory with its originating scope
        so scoped chat queries can filter out unrelated context. When omitted,
        the memory is treated as global / unscoped.
        """
        resolved_importance = float(
            settings.MEMORY_INITIAL_IMPORTANCE if importance is None else importance
        )
        resolved_importance = max(
            float(settings.MEMORY_MIN_IMPORTANCE),
            min(float(settings.MEMORY_MAX_IMPORTANCE), resolved_importance),
        )

        # HIGH-11: Serialize writes to the same key so two concurrent set()
        # calls cannot interleave their Step 2 (SQL) / Step 3 (vector)
        # sequences and leave orphaned vectors.
        mutation_locks = {
            id(lock): lock
            for lock in (
                _get_key_lock(user_id, candidate) for candidate in [key, *(supersedes or [])]
            )
        }
        with ExitStack() as stack:
            for lock_id in sorted(mutation_locks):
                stack.enter_context(mutation_locks[lock_id])
            self._set_locked(
                user_id,
                key,
                value,
                source,
                resolved_importance,
                expires_at,
                category,
                session_id,
                turn_index,
                meeting_ids,
                file_ids,
                1.0 if confidence is None else confidence,
                valid_from,
                valid_to,
                evidence_message_ids,
                evidence_excerpt,
                evidence_refs,
                conflicts_with,
                supersedes,
                fact_type,
                assertion_status,
                project_id,
                subject,
                predicate,
                object_value,
                action_status,
                assignee,
                due_at,
                expected_revision,
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
        confidence: float,
        valid_from: str | None,
        valid_to: str | None,
        evidence_message_ids: list[int] | None,
        evidence_excerpt: str | None,
        evidence_refs: list[dict[str, Any]] | None,
        conflicts_with: list[str] | None,
        supersedes: list[str] | None,
        fact_type: str,
        assertion_status: str,
        project_id: str | None,
        subject: str | None,
        predicate: str | None,
        object_value: str | None,
        action_status: str | None,
        assignee: str | None,
        due_at: str | None,
        expected_revision: int | None,
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
                confidence=confidence,
                valid_from=valid_from,
                valid_to=valid_to,
                evidence_message_ids=evidence_message_ids,
                evidence_excerpt=evidence_excerpt,
                evidence_refs=evidence_refs,
                conflicts_with=conflicts_with,
                fact_type=fact_type,
                assertion_status=assertion_status,
                project_id=project_id,
                subject=subject,
                predicate=predicate,
                object_value=object_value or value,
                action_status=action_status,
                assignee=assignee,
                due_at=due_at,
                expected_revision=expected_revision,
            )
            if session_id is not None:
                conn.execute(
                    "UPDATE user_memories SET session_id=?, turn_index=? WHERE user_id=? AND key=?",
                    (session_id, turn_index, user_id, key),
                )
            # The SQL fact and its derived-index state commit atomically. A
            # crash after this transaction is therefore discoverable by the
            # startup reconciler instead of leaving an unmarked stale vector.
            conn.execute(
                "UPDATE user_memories SET vector_state=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND key=?",
                ("pending" if assertion_status == "confirmed" else "inactive", user_id, key),
            )
            for old_key in dict.fromkeys(supersedes or []):
                if old_key != key:
                    db.mark_memory_superseded(
                        conn,
                        user_id=user_id,
                        key=old_key,
                        superseded_by=key,
                    )

        from ._index_sync import index_current_memory

        index_current_memory(user_id, key)

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
        with _get_key_lock(user_id, key):
            embedding_id: str | None = None
            with get_write_connection() as conn:
                embedding_id = db.delete_memory(conn, user_id=user_id, key=key)
                if embedding_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO pending_vector_deletions "
                        "(collection, embedding_id) VALUES ('memory', ?)",
                        (embedding_id,),
                    )

            if embedding_id:
                # The outbox row was committed atomically with the primary delete.
                # Keep the key lock until the immediate cleanup attempt finishes so
                # a concurrent set cannot have its new vector removed by this delete.
                try:
                    cleanup_pending_vector_deletions(collections={"memory"})
                except Exception:
                    logger.warning(
                        "Immediate memory vector cleanup failed; durable job remains queued",
                        exc_info=True,
                    )
        audit_log("memory.delete", "memory", key, user_id=user_id)

    def delete_many(self, user_id: str, keys: list[str]) -> tuple[list[str], list[str]]:
        """Delete several memories atomically and enqueue their vector cleanup work."""
        unique_keys = list(dict.fromkeys(keys))
        deleted: list[str] = []
        locks = sorted({_get_key_lock(user_id, key) for key in unique_keys}, key=id)
        with ExitStack() as stack:
            for lock in locks:
                stack.enter_context(lock)
            with get_write_connection() as conn:
                existing = db.get_memories_batch(conn, user_id=user_id, keys=unique_keys)
                for key in unique_keys:
                    if key not in existing:
                        continue
                    embedding_id = db.delete_memory(conn, user_id=user_id, key=key)
                    if embedding_id:
                        conn.execute(
                            "INSERT OR IGNORE INTO pending_vector_deletions "
                            "(collection, embedding_id) VALUES ('memory', ?)",
                            (embedding_id,),
                        )
                    deleted.append(key)

            if deleted:
                try:
                    cleanup_pending_vector_deletions(collections={"memory"})
                except Exception:
                    logger.warning(
                        "Immediate batch memory vector cleanup failed; durable jobs remain queued",
                        exc_info=True,
                    )
                for key in deleted:
                    audit_log("memory.delete", "memory", key, user_id=user_id)
        missing = [key for key in unique_keys if key not in existing]
        return deleted, missing

    @staticmethod
    def _queue_pending_vector_deletion(collection: str, embedding_id: str) -> None:
        """Record a failed vector deletion for later cleanup."""
        try:
            with get_write_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions "
                    "(collection, embedding_id) VALUES (?, ?)",
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
        """Bound active recall without deleting the business/history ledger."""
        from ....core.database.memory_lifecycle import archive_memories

        with get_write_connection() as conn:
            rows = conn.execute(
                "SELECT id,embedding_id FROM user_memories "
                "WHERE user_id=? AND archived_at IS NULL AND key!='__profile__' "
                "ORDER BY salience DESC, created_at DESC, id DESC LIMIT -1 OFFSET ?",
                (user_id, settings.MEMORY_MAX_PER_USER),
            ).fetchall()
            archive_memories(conn, rows, reason="recall_capacity")

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


def purge_expired_memories_durable() -> int:
    """Delete expired memories while durably scheduling vector cleanup."""
    with get_write_connection() as conn:
        expired = db.get_expired_memory_ids(conn)
        conn.executemany(
            "INSERT OR IGNORE INTO pending_vector_deletions "
            "(collection, embedding_id) VALUES ('memory', ?)",
            [(row["embedding_id"],) for row in expired if row.get("embedding_id")],
        )
        deleted = db.delete_expired_memories(conn)
    if expired:
        try:
            cleanup_pending_vector_deletions(collections={"memory"})
        except Exception:
            logger.warning(
                "Immediate expired-memory vector cleanup failed; durable jobs remain queued",
                exc_info=True,
            )
    return deleted


def cleanup_pending_vector_deletions(
    *,
    collections: set[str] | None = None,
    deletion_batch_id: str | None = None,
) -> int:
    """Process any deferred vector deletions that failed previously.

    Called during startup.  Returns the number of vectors successfully deleted.
    Rows that fail more than ``settings.VECTOR_DELETION_MAX_ATTEMPTS`` times
    are moved to dead-letter status and removed from the retry queue.
    """
    from ....core.config import settings

    max_attempts = settings.VECTOR_DELETION_MAX_ATTEMPTS

    query = (
        "SELECT id, collection, embedding_id, COALESCE(attempts, 0) AS attempts "
        "FROM pending_vector_deletions WHERE COALESCE(status, 'pending') = 'pending' "
        "AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)"
    )
    params: list[str] = []
    if collections:
        ordered_collections = tuple(sorted(collections))
        placeholders = ",".join("?" for _ in ordered_collections)
        query += f" AND collection IN ({placeholders})"
        params.extend(ordered_collections)
    if deletion_batch_id is not None:
        query += " AND deletion_batch_id = ?"
        params.append(deletion_batch_id)

    lease_owner = uuid.uuid4().hex
    with get_write_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        if rows:
            row_ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in row_ids)
            conn.execute(
                "UPDATE pending_vector_deletions SET lease_owner=?, "
                "lease_expires_at=datetime('now', '+30 minutes'), "
                "updated_at=CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders}) "
                "AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)",
                (lease_owner, *row_ids),
            )
            claimed = conn.execute(
                "SELECT id, collection, embedding_id, COALESCE(attempts, 0) AS attempts "
                "FROM pending_vector_deletions WHERE lease_owner=?",
                (lease_owner,),
            ).fetchall()
            rows = claimed

    if not rows:
        return 0

    cleaned = 0
    dead_ids: list[int] = []
    failures: dict[int, str] = {}
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
            elif collection == "session_summary":
                from .._summary_vectorstore import get_summary_vectorstore

                vs = get_summary_vectorstore()
                vs.delete(embedding_id)
            elif collection == "file":
                from pathlib import Path

                from ....core.config import settings

                target = Path(embedding_id).resolve()
                upload_root = settings.UPLOAD_DIR.resolve()
                if not target.is_relative_to(upload_root) or target == upload_root:
                    raise ValueError(f"Unsafe pending file deletion path: {target}")
                target.unlink(missing_ok=True)
            elif collection == "directory":
                import shutil
                from pathlib import Path

                from ....core.config import settings

                target = Path(embedding_id).resolve()
                upload_root = settings.UPLOAD_DIR.resolve()
                if not target.is_relative_to(upload_root) or target == upload_root:
                    raise ValueError(f"Unsafe pending directory deletion path: {target}")
                if target.exists():
                    shutil.rmtree(target)
            elif collection == "meeting":
                from ...rag._indexer_store import retry_pending_index_deletion

                retry_pending_index_deletion(collection, f"meeting_{int(embedding_id)}")
            elif collection in {"chroma", "bm25", "summary", "raganything"}:
                from ...rag._indexer_store import retry_pending_index_deletion

                retry_pending_index_deletion(collection, embedding_id)
            else:
                raise ValueError(f"Unknown pending deletion collection: {collection!r}")
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
            failures[row_id] = f"{type_name}: {exc}"[:1000]

    # Single write transaction: remove successful rows, retain failures, and
    # preserve exhausted entries as inspectable dead letters.
    to_delete = [
        row["id"] for row in rows if row["id"] not in failures and row["id"] not in dead_ids
    ]
    if to_delete or failures or dead_ids:
        with get_write_connection() as conn:
            if to_delete:
                placeholders = ",".join("?" for _ in to_delete)
                conn.execute(
                    f"DELETE FROM pending_vector_deletions WHERE id IN ({placeholders}) "
                    "AND lease_owner=?",
                    (*to_delete, lease_owner),
                )
            for row_id, last_error in failures.items():
                conn.execute(
                    "UPDATE pending_vector_deletions SET attempts = attempts + 1, "
                    "last_error = ?, updated_at = CURRENT_TIMESTAMP, "
                    "lease_owner=NULL, lease_expires_at=NULL "
                    "WHERE id = ? AND lease_owner = ?",
                    (last_error, row_id, lease_owner),
                )
            if dead_ids:
                placeholders = ",".join("?" for _ in dead_ids)
                conn.execute(
                    "UPDATE pending_vector_deletions SET status = 'dead_letter', "
                    "updated_at = CURRENT_TIMESTAMP, lease_owner=NULL, lease_expires_at=NULL "
                    f"WHERE id IN ({placeholders}) AND lease_owner=?",
                    (*dead_ids, lease_owner),
                )

    if cleaned:
        logger.info("Cleaned up %d pending vector deletions", cleaned)
        from ....core.metrics import VECTOR_DELETION_CLEANED_TOTAL

        VECTOR_DELETION_CLEANED_TOTAL.labels(collection="all").inc(cleaned)

    if dead_ids:
        logger.warning(
            "Moved %d permanently stuck vector deletions to dead letter (exceeded %d attempts)",
            len(dead_ids),
            max_attempts,
        )
        from ....core.metrics import PENDING_VECTOR_DELETIONS_DEAD_LETTER_TOTAL

        PENDING_VECTOR_DELETIONS_DEAD_LETTER_TOTAL.inc(len(dead_ids))

    return cleaned


def requeue_pending_memory_vectors() -> int:
    """Recover committed pending facts without rewriting scope or revision."""
    from ._index_sync import reconcile_memory_vectors

    return reconcile_memory_vectors()
