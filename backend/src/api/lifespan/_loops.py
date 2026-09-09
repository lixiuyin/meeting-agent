"""Background task loops that run for the lifetime of the application."""

import asyncio
import contextlib
import time

from ...core.config import settings
from ._shared import logger, record_best_effort_failure


async def expired_purge_loop() -> None:
    """Periodic expired-memory purge (every hour)."""
    from ...services.memory._service._crud import purge_expired_memories_durable

    while True:
        await asyncio.sleep(3600)
        try:
            deleted = await asyncio.to_thread(purge_expired_memories_durable)
            if deleted:
                logger.info("Periodic purge: cleaned up %d expired memories", deleted)
        except Exception:
            record_best_effort_failure("periodic_expired_memory_purge")
            logger.warning("Periodic expired-memory purge failed", exc_info=True)


async def summarize_unsummarized() -> None:
    """Generate summaries for sessions that don't have one yet."""
    started = time.monotonic()
    try:
        from ...services.memory import session_summary_service

        count = await session_summary_service.summarize_unsummarized()
        elapsed = time.monotonic() - started
        logger.info(
            "Startup session summarization done: generated=%d elapsed=%.2fs",
            count,
            elapsed,
        )
    except Exception:
        record_best_effort_failure("startup_session_summarize")
        logger.warning("Startup session summarization failed", exc_info=True)


async def idle_summary_loop() -> None:
    """Periodically summarize sessions idle beyond the configured threshold."""
    interval = max(settings.SESSION_SUMMARY_IDLE_MINUTES * 60, 300)
    while True:
        await asyncio.sleep(interval)
        try:
            from ...services.memory import session_summary_service

            await session_summary_service.summarize_idle_sessions()
        except Exception:
            record_best_effort_failure("idle_session_summarize")
            logger.warning("Idle session summarization failed", exc_info=True)


def checkpoint_wal_once() -> tuple[int, int, int]:
    from ...core.database import get_connection

    with get_connection() as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        result = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        busy, log, ckpt = int(result[0]), int(result[1]), int(result[2])
        if busy == 0 and ckpt == log and log > 0:
            result2 = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            return int(result2[0]), int(result2[1]), int(result2[2])
        return busy, log, ckpt


async def wal_checkpoint_loop() -> None:
    """Periodic WAL checkpoint to prevent .db-wal file bloat (every hour).

    Uses PASSIVE mode first (safe with concurrent readers), then escalates
    to TRUNCATE when no readers are active to reclaim disk space.

    After 3 consecutive failures, forcibly closes the oldest read connection
    and retries.
    """
    _consecutive_failures = 0
    while True:
        await asyncio.sleep(3600)
        try:
            result = await asyncio.wait_for(asyncio.to_thread(checkpoint_wal_once), timeout=10)
            logger.debug(
                "WAL checkpoint completed: busy=%d, log=%d, checkpointed=%d",
                result[0],
                result[1],
                result[2],
            )
            _consecutive_failures = 0
        except TimeoutError:
            _consecutive_failures += 1
            record_best_effort_failure("wal_checkpoint_timeout")
            from ...core.metrics import WAL_CHECKPOINT_FAILURES_TOTAL

            WAL_CHECKPOINT_FAILURES_TOTAL.inc()
            logger.warning(
                "WAL checkpoint timed out (consecutive=%d)",
                _consecutive_failures,
            )
            if _consecutive_failures >= 3:
                _force_close_stale_read_connections()
        except Exception:
            _consecutive_failures += 1
            record_best_effort_failure("wal_checkpoint_failed")
            from ...core.metrics import WAL_CHECKPOINT_FAILURES_TOTAL

            WAL_CHECKPOINT_FAILURES_TOTAL.inc()
            logger.warning(
                "WAL checkpoint failed (consecutive=%d)",
                _consecutive_failures,
                exc_info=True,
            )
            if _consecutive_failures >= 3:
                _force_close_stale_read_connections()


def _force_close_stale_read_connections() -> None:
    """Close the most-idle read connection to unblock WAL checkpoint."""
    from ...core.database._connection import (
        _conn_active,
        _conn_last_active,
        _connections,
        _pool_lock,
    )

    now = time.monotonic()
    with _pool_lock:
        idle = [(tid, conn) for tid, conn in _connections.items() if _conn_active.get(tid, 0) == 0]
        if not idle:
            logger.warning("No idle read connection is safe to close for WAL recovery")
            return
        oldest_tid, oldest_conn = max(
            idle,
            key=lambda x: now - _conn_last_active.get(x[0], 0),
        )
        try:
            with contextlib.suppress(Exception):
                oldest_conn.interrupt()
            with contextlib.suppress(Exception):
                oldest_conn.rollback()
            oldest_conn.close()
            _connections.pop(oldest_tid, None)
            _conn_last_active.pop(oldest_tid, None)
            _conn_active.pop(oldest_tid, None)
            logger.warning(
                "Closed oldest read connection (thread=%d) to unblock WAL checkpoint",
                oldest_tid,
            )
        except Exception:
            logger.warning(
                "Failed to close stale read connection (thread=%d)",
                oldest_tid,
                exc_info=True,
            )


async def index_reconcile_loop() -> None:
    """Periodic index-state reconciliation for multimodal consistency."""
    while True:
        await asyncio.sleep(600)
        try:
            from ...services.rag import reconcile_multimodal_index_state

            await asyncio.to_thread(reconcile_multimodal_index_state, limit=1000)
        except Exception:
            record_best_effort_failure("index_reconcile_failed")
            logger.warning("Index-state reconciliation failed", exc_info=True)
        try:
            from ...services.rag import check_and_rebuild_bm25_if_drifted

            await asyncio.to_thread(check_and_rebuild_bm25_if_drifted)
        except Exception:
            logger.debug("Periodic BM25 drift check failed", exc_info=True)
        try:
            from ...services.rag._indexer_store import count_legacy_chunks_without_file_id

            legacy = await asyncio.to_thread(count_legacy_chunks_without_file_id)
            if legacy.get("vector") or legacy.get("bm25"):
                logger.info(
                    "Legacy chunks without file_id: vector=%d bm25=%d "
                    "(run migrate_bm25_metadata.py --db <database-path> to backfill)",
                    legacy.get("vector", 0),
                    legacy.get("bm25", 0),
                )
        except Exception:
            logger.debug("Legacy chunk count query failed", exc_info=True)
        try:
            from ...services.memory._summary_vectorstore import sync_missing_summary_vectors

            repaired = await asyncio.to_thread(sync_missing_summary_vectors, repair_limit=100)
            if repaired:
                logger.info("Periodic session-summary vector sync repaired %d rows", repaired)
        except Exception:
            record_best_effort_failure("session_summary_vector_sync_failed")
            logger.warning("Periodic session-summary vector sync failed", exc_info=True)


async def retention_loop() -> None:
    """Daily data retention purge."""
    from ...services.retention import (
        purge_old_chat_messages,
        purge_old_decay_state,
        purge_stale_low_importance_memories,
    )

    while True:
        await asyncio.sleep(86400)
        try:
            # SQLite permits a single writer. Run each write job off the event
            # loop, but keep them sequential to avoid needless lock contention.
            msgs = await asyncio.to_thread(purge_old_chat_messages)
            states = await asyncio.to_thread(purge_old_decay_state)
            mems = await asyncio.to_thread(purge_stale_low_importance_memories)
            if msgs or states or mems:
                logger.info(
                    "Retention purge: %d chat messages, %d decay states, %d memories removed",
                    msgs,
                    states,
                    mems,
                )
        except Exception:
            record_best_effort_failure("retention_purge_failed")
            logger.warning("Retention purge failed", exc_info=True)

        # Also sweep expired audit logs and pending vector deletions.
        try:
            from ...core.database import cleanup_expired_audit_logs, get_write_connection

            def _cleanup_audit_logs() -> int:
                with get_write_connection() as conn:
                    return cleanup_expired_audit_logs(conn)

            audit_deleted = await asyncio.to_thread(_cleanup_audit_logs)
            if audit_deleted:
                logger.info("Retention purge: %d expired audit logs removed", audit_deleted)
        except Exception:
            logger.debug("Audit log cleanup failed", exc_info=True)
        try:
            from ...services.memory._service._crud import cleanup_pending_vector_deletions

            cleaned = await asyncio.to_thread(cleanup_pending_vector_deletions)
            if cleaned:
                logger.info("Periodic vector deletion cleanup: %d cleaned", cleaned)
        except Exception:
            logger.debug("Periodic vector deletion cleanup failed", exc_info=True)


async def stale_recovery_loop() -> None:
    """Periodic stale-meeting recovery (every 15 min)."""
    from ...services.processor import recover_stale_meetings, resume_interrupted_processing
    from ...services.processor._scheduler import active_processing_file_ids

    while True:
        await asyncio.sleep(15 * 60)
        try:
            await resume_interrupted_processing(stale_only=True)
            await asyncio.to_thread(
                recover_stale_meetings,
                active_file_ids=active_processing_file_ids(),
            )
        except Exception:
            record_best_effort_failure("periodic_stale_recovery")
            logger.warning("Periodic stale recovery failed", exc_info=True)


async def bm25_drift_loop() -> None:
    """Periodic BM25/Chroma drift reconciliation (every 6 hours)."""
    from ...services.rag._bm25_maintenance import check_and_rebuild_bm25_if_drifted

    while True:
        await asyncio.sleep(6 * 3600)
        try:
            await asyncio.to_thread(check_and_rebuild_bm25_if_drifted)
        except Exception:
            record_best_effort_failure("periodic_bm25_drift_check")
            logger.warning("Periodic BM25 drift check failed", exc_info=True)
