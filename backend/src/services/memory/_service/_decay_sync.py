"""MemoryService decay and vector-sync mixin."""

import calendar
import time
from datetime import UTC, datetime

from ....core import database as db
from ....core.metrics import MEMORY_DECAY_PURGED, MEMORY_DECAY_RUN_TOTAL
from .._common import logger
from .._decay import _compute_decay_score, _get_last_decay_time, _set_last_decay_time
from . import settings

_GC_MAX_FRESHNESS = 0.1
_GC_MAX_IDLE_DAYS = 60
_SYNC_BATCH_SIZE = 10  # MEM-4: throttle embedding calls per batch
_SYNC_BATCH_DELAY_S = 0.5  # Pause between batches to avoid API rate limits


class _MemoryDecaySyncMixin:
    def decay_memories(self, user_id: str = "default") -> int:
        """Apply continuous decay to freshness without mutating fact salience.

        Uses batched UPDATE via executemany to avoid the per-row overhead
        of the old read-compute-write loop (M-C5).
        """
        if not settings.MEMORY_DECAY_ENABLED:
            return 0
        try:
            updates: list[tuple[float, str, str, int, float, str | None]] = []
            last_decay = _get_last_decay_time(user_id)
            with db.get_connection() as conn:
                # Maintenance must scan the full user corpus.  The public list
                # API defaults to a page of 100 rows and is intentionally not
                # an appropriate maintenance cursor.
                memories = db.list_memories(
                    conn, user_id=user_id, include_expired=False, limit=None
                )
                for m in memories:
                    key = m.get("key")
                    if not isinstance(key, str) or not key:
                        continue
                    freshness = float(m.get("freshness_score", 1.0))
                    # Freshness is persisted after each decay run.  Decay only
                    # over the interval since the most recent of creation,
                    # access, and the previous run; using the full age on every
                    # run compounds the same elapsed time repeatedly.
                    reference = (
                        _latest_reference_timestamp(
                            last_decay,
                            m.get("created_at"),
                            m.get("last_accessed"),
                        )
                        if last_decay
                        else (m.get("last_accessed") or m.get("created_at"))
                    )
                    decayed = _compute_decay_score(
                        freshness,
                        reference,
                        expires_at=m.get("valid_to") or m.get("expires_at"),
                    )
                    if abs(float(decayed) - freshness) < 0.01:
                        continue
                    updates.append(
                        (
                            float(decayed),
                            user_id,
                            key,
                            int(m.get("revision", 1)),
                            freshness,
                            m.get("last_confirmed_at"),
                        )
                    )

            if updates:
                with db.get_write_connection() as conn:
                    # Fence both fact revision and confirmation timestamp. A
                    # reconfirmation may reset freshness to the same value it
                    # had in our snapshot, so freshness alone is insufficient.
                    cursor = conn.executemany(
                        "UPDATE user_memories SET freshness_score=?, "
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE user_id=? AND key=? AND revision=? "
                        "AND freshness_score=? AND last_confirmed_at IS ?",
                        [
                            (new_score, uid, key, revision, old_score, confirmed_at)
                            for new_score, uid, key, revision, old_score, confirmed_at in updates
                        ],
                    )
                    applied = max(0, cursor.rowcount)
                logger.info("Updated freshness for %d memories of user %s", applied, user_id)
            else:
                applied = 0

            _set_last_decay_time(user_id)
            MEMORY_DECAY_RUN_TOTAL.labels(status="success").inc()
            return applied
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
        """Reconcile current pending/failed revisions with bounded backoff."""
        from ._index_sync import reconcile_memory_vectors

        return reconcile_memory_vectors()

    def purge_stale_memories(self, user_id: str = "default") -> int:
        """Archive recall for memories that are effectively dead: very low importance
        and not accessed in a long time. Also clean up superseded memories.

        Runs after decay so importance scores are up-to-date.
        """
        now_ts = time.time()
        deleted = 0
        with db.get_write_connection() as conn:
            memories = db.list_memories(conn, user_id=user_id, include_expired=False, limit=None)
            for m in memories:
                key = m.get("key")
                if not isinstance(key, str) or not key or m.get("archived_at"):
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

                # Remove only low-salience facts that are also stale.  Age alone
                # never erases the user's explicit importance judgment.
                salience = float(m.get("salience", m.get("importance", 3)))
                freshness = _compute_decay_score(
                    float(m.get("freshness_score", 1.0)),
                    m.get("last_accessed") or m.get("last_confirmed_at") or m.get("created_at"),
                    expires_at=m.get("valid_to") or m.get("expires_at"),
                )
                if salience > 1.0 or freshness > _GC_MAX_FRESHNESS:
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

            logger.info("GC archived %d stale memories for user %s", deleted, user_id)
            MEMORY_DECAY_PURGED.inc(deleted)
            audit_log(
                "purge_stale",
                "memory",
                user_id,
                user_id=user_id,
                detail=f"archived={deleted}",
            )
        return deleted

    @staticmethod
    def _gc_delete_memory(conn, user_id: str, key: str, memory: dict) -> None:
        from ....core.database.memory_lifecycle import archive_memories

        archive_memories(conn, [memory], reason="stale_recall")


def _latest_reference_timestamp(*values: object) -> str | None:
    """Return the latest valid UTC SQLite/ISO timestamp from *values*."""
    latest: datetime | None = None
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        candidate: datetime | None = None
        with_value = value.replace("Z", "+00:00")
        try:
            candidate = datetime.fromisoformat(with_value)
        except ValueError:
            continue
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=UTC)
        else:
            candidate = candidate.astimezone(UTC)
        if latest is None or candidate > latest:
            latest = candidate
    return latest.strftime("%Y-%m-%d %H:%M:%S") if latest else None
