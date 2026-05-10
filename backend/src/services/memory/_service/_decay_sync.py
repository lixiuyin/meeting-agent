"""MemoryService decay and vector-sync mixin."""

import calendar
import time
from datetime import UTC, datetime

from ....core import database as db
from ....core.database import get_write_connection
from ....core.metrics import MEMORY_DECAY_PURGED, MEMORY_DECAY_RUN_TOTAL
from .._common import _INITIAL_IMPORTANCE, logger
from .._decay import _compute_decay_score, _get_last_decay_time, _set_last_decay_time
from .._vectorstore import get_memory_vectorstore
from . import settings

_GC_MIN_IMPORTANCE = 0.1
_GC_MAX_IDLE_DAYS = 60
_SYNC_BATCH_SIZE = 10  # MEM-4: throttle embedding calls per batch
_SYNC_BATCH_DELAY_S = 0.5  # Pause between batches to avoid API rate limits


class _MemoryDecaySyncMixin:
    def decay_memories(self, user_id: str = "default") -> int:
        """Apply continuous decay and persist decayed importance scores.

        Uses batched UPDATE via executemany to avoid the per-row overhead
        of the old read-compute-write loop (M-C5).
        """
        if not settings.MEMORY_DECAY_ENABLED:
            return 0
        try:
            updates: list[tuple[float, str, str, float]] = []
            with db.get_write_connection() as conn:
                memories = db.list_memories(conn, user_id=user_id, include_expired=False)
                for m in memories:
                    key = m.get("key")
                    if not isinstance(key, str) or not key:
                        continue
                    importance = m.get("importance", _INITIAL_IMPORTANCE)
                    decayed = _compute_decay_score(
                        importance,
                        m.get("last_accessed"),
                        created_at=m.get("created_at"),
                        expires_at=m.get("expires_at"),
                    )
                    if abs(float(decayed) - float(importance)) < 0.01:
                        continue
                    updates.append((float(decayed), user_id, key, float(importance)))

            if updates:
                with db.get_write_connection() as conn:
                    # Optimistic concurrency: skip when importance was boosted
                    # between read and write (current > observed). This avoids
                    # permanently losing a boost — the decay will apply on the
                    # next cycle instead of being silently dropped.
                    conn.executemany(
                        "UPDATE user_memories SET importance=?, "
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE user_id=? AND key=? AND importance <= ?",
                        [(new_imp, uid, key, old_imp) for new_imp, uid, key, old_imp in updates],
                    )
                logger.info("Decayed importance for %d memories of user %s", len(updates), user_id)

            _set_last_decay_time(user_id)
            MEMORY_DECAY_RUN_TOTAL.labels(status="success").inc()
            return len(updates)
        except Exception:
            MEMORY_DECAY_RUN_TOTAL.labels(status="error").inc()
            raise

    def decay_memories_if_needed(self, user_id: str = "default") -> int:
        """Decay memories only if MEMORY_DECAY_ENABLED and 24h interval elapsed."""
        if not settings.MEMORY_DECAY_ENABLED:
            return 0
        last = _get_last_decay_time(user_id)
        if last:
            try:
                last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                elapsed = datetime.now(UTC) - last_dt
                if elapsed.total_seconds() < settings.MEMORY_DECAY_INTERVAL_HOURS * 3600:
                    return 0
            except (ValueError, TypeError):
                pass
        return self.decay_memories(user_id)

    def sync_missing_vectors(self) -> int:
        """Re-index memories with NULL embedding_id or vector_state='failed' (CRITICAL-3).

        Covers two scenarios:
        1. embedding_id IS NULL — crash before vector upsert completed
        2. vector_state='failed' — online retry exhausted, needs offline reconciliation
        """
        # Check if vector_state column exists before using it in a query.
        # This avoids an OperationalError + spurious ERROR log in environments
        # where migration 43 has not been applied yet.
        _has_vector_state = False
        try:
            with db.get_connection() as conn:
                cols = conn.execute("PRAGMA table_info(user_memories)").fetchall()
                _has_vector_state = any(c[1] == "vector_state" for c in cols)
        except Exception:
            logger.debug(
                "PRAGMA table_info check failed; assuming no vector_state column",
                exc_info=True,
            )

        if _has_vector_state:
            with db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT user_id, key, value, importance, category FROM user_memories "
                    "WHERE superseded_by IS NULL "
                    "AND (embedding_id IS NULL OR vector_state='failed')"
                ).fetchall()
        else:
            with db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT user_id, key, value, importance, category FROM user_memories "
                    "WHERE embedding_id IS NULL AND superseded_by IS NULL"
                ).fetchall()

        count = 0
        for i, row in enumerate(rows):
            try:
                vs = get_memory_vectorstore()
                # sqlite3.Row supports subscript access only, not dict.get();
                # the SELECT above always returns importance and category, so
                # read them directly. ``category`` may be NULL — pass through
                # as None.
                importance_value = row["importance"]
                if importance_value is None:
                    importance_value = 3
                embedding_id = vs.upsert(
                    row["user_id"],
                    row["key"],
                    row["value"],
                    importance=importance_value,
                    category=row["category"],
                )
                if embedding_id:
                    with get_write_connection() as conn:
                        try:
                            conn.execute(
                                "UPDATE user_memories SET embedding_id=?, vector_state='synced' "
                                "WHERE user_id=? AND key=?",
                                (embedding_id, row["user_id"], row["key"]),
                            )
                        except Exception:
                            # vector_state column may not exist yet
                            conn.execute(
                                "UPDATE user_memories SET embedding_id=? WHERE user_id=? AND key=?",
                                (embedding_id, row["user_id"], row["key"]),
                            )
                    count += 1
                # MEM-4: Throttle — pause between batches to avoid embedding API floods
                if (i + 1) % _SYNC_BATCH_SIZE == 0 and i + 1 < len(rows):
                    time.sleep(_SYNC_BATCH_DELAY_S)
            except Exception:
                logger.warning(
                    "Failed to re-index memory vector for user=%s key=%s",
                    row["user_id"],
                    row["key"],
                    exc_info=True,
                )
        if count:
            logger.info("Re-indexed %d missing memory vectors", count)
        return count

    def purge_stale_memories(self, user_id: str = "default") -> int:
        """Hard-delete memories that are effectively dead: very low importance
        and not accessed in a long time. Also clean up superseded memories.

        Runs after decay so importance scores are up-to-date.
        """
        now_ts = time.time()
        deleted = 0
        with db.get_write_connection() as conn:
            memories = db.list_memories(conn, user_id=user_id, include_expired=False)
            for m in memories:
                key = m.get("key")
                if not isinstance(key, str) or not key:
                    continue

                # Remove superseded memories older than 30 days
                if m.get("superseded_by"):
                    updated_at = m.get("updated_at", "")
                    try:
                        updated_ts = calendar.timegm(time.strptime(updated_at, "%Y-%m-%d %H:%M:%S"))
                        if (now_ts - updated_ts) / 86400 > 30:
                            self._gc_delete_memory(conn, user_id, key, m)
                            deleted += 1
                    except (ValueError, TypeError):
                        pass
                    continue

                # Remove memories with near-zero importance and stale access
                importance = float(m.get("importance", _INITIAL_IMPORTANCE))
                if importance > _GC_MIN_IMPORTANCE:
                    continue

                last_accessed = m.get("last_accessed")
                if not last_accessed:
                    continue
                try:
                    last_ts = calendar.timegm(time.strptime(last_accessed, "%Y-%m-%d %H:%M:%S"))
                    idle_days = (now_ts - last_ts) / 86400
                    if idle_days > _GC_MAX_IDLE_DAYS:
                        self._gc_delete_memory(conn, user_id, key, m)
                        deleted += 1
                except (ValueError, TypeError):
                    continue

        if deleted:
            from ....core.audit import audit_log

            logger.info("GC purged %d stale memories for user %s", deleted, user_id)
            MEMORY_DECAY_PURGED.inc(deleted)
            audit_log(
                "purge_stale",
                "memory",
                user_id,
                user_id=user_id,
                detail=f"deleted={deleted}",
            )
        return deleted

    @staticmethod
    def _gc_delete_memory(conn, user_id: str, key: str, memory: dict) -> None:
        """Delete a memory row and queue its vector for cleanup."""
        embedding_id = memory.get("embedding_id")
        db.delete_memory(conn, user_id=user_id, key=key)
        if embedding_id:
            try:
                conn.execute(
                    "INSERT INTO pending_vector_deletions (collection, embedding_id) "
                    "VALUES ('memory', ?)",
                    (embedding_id,),
                )
            except Exception:
                logger.warning(
                    "Failed to queue vector deletion for GC'd memory %s", key, exc_info=True
                )
