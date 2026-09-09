"""Application lifecycle manager.

The lifespan context manager orchestrates startup (critical + best-effort)
and graceful shutdown. Sub-modules split the logic:

- ``_critical``: DB migration, LLM, embeddings, vectorstore
- ``_loops``: background task loops (purge, decay, checkpoint, etc.)
- ``_shared``: helpers (failure recording, GeneratorExit suppression)
- ``_shutdown``: graceful cleanup
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ...core.config import settings
from ...core.database import get_write_connection
from ...utils.supervised_task import get_background_tasks
from ._critical import run_alembic_upgrade, run_critical_startup
from ._loops import (
    bm25_drift_loop,
    expired_purge_loop,
    idle_summary_loop,
    index_reconcile_loop,
    retention_loop,
    stale_recovery_loop,
    summarize_unsummarized,
    wal_checkpoint_loop,
)
from ._shared import record_best_effort_failure, suppress_generator_exit_errors
from ._shutdown import graceful_shutdown

logger = logging.getLogger(__name__)

_bg = get_background_tasks()
_critical_startup_error: str | None = None


def get_critical_startup_error() -> str | None:
    """Return the current process' critical startup failure, if any."""
    return _critical_startup_error


def _startup_summary_backfill_enabled() -> bool:
    return settings.SESSION_SUMMARY_ENABLED and settings.SESSION_SUMMARY_STARTUP_BACKFILL


async def _recover_incomplete_file_summaries() -> tuple[int, int]:
    """Recover file-summary lifecycle state after a process restart.

    When automatic summaries are enabled, interrupted or never-started
    summaries are requeued.  When they are disabled, parsed files must not be
    left in the transient ``summarizing`` state: restore their ingest status to
    ``ready`` while retaining ``summary_status='pending'`` so a later manual
    summary remains possible.

    Returns ``(requeued, normalized)`` for startup diagnostics and tests.
    """
    from ...core.database import get_connection
    from ...services.processor._pipeline_common import _update_meeting_status_from_files

    def _find_incomplete() -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, meeting_id FROM meeting_files "
                "WHERE (status='summarizing' OR "
                "(status='ready' AND summary_status NOT IN ('ready','failed')))"
            ).fetchall()
        return [dict(row) for row in rows]

    incomplete = await asyncio.to_thread(_find_incomplete)
    if settings.MEETING_AUTO_SUMMARIZE_FILES:
        from ...services.processor._pipeline_common import schedule_post_ready_summary

        for file_row in incomplete:
            await schedule_post_ready_summary(file_row["id"], file_row["meeting_id"])
        return len(incomplete), 0

    def _normalize_disabled() -> int:
        meeting_ids = {int(row["meeting_id"]) for row in incomplete}
        with get_write_connection() as conn:
            stale_meetings = conn.execute(
                "SELECT id FROM meetings WHERE status='summarizing'"
            ).fetchall()
            meeting_ids.update(int(row["id"]) for row in stale_meetings)
            cursor = conn.execute(
                "UPDATE meeting_files SET status='ready', updated_at=CURRENT_TIMESTAMP "
                "WHERE status='summarizing'"
            )
            for meeting_id in meeting_ids:
                _update_meeting_status_from_files(conn, meeting_id)
            return max(cursor.rowcount, 0)

    normalized = await asyncio.to_thread(_normalize_disabled)
    return 0, normalized


async def _prewarm_skill_matcher_once(loader, matcher) -> tuple[int, int]:
    """Warm the semantic skill corpus in one provider batch.

    Corpus embeddings hold a matcher-wide lock while the remote provider is
    running.  Warming one skill at a time therefore makes an early chat wait
    behind several serial network calls.  The matcher already supports a
    single ordered batch, so use that path before the lower-priority query
    endpoint warm-up.
    """
    summaries = loader.load_summaries() if hasattr(loader, "load_summaries") else []
    semantic_summaries = [
        summary
        for summary in summaries
        if getattr(summary, "intent_matching", None)
        and summary.intent_matching.method in ("semantic", "hybrid")
        and getattr(summary.intent_matching, "examples", None)
    ]
    cached = await asyncio.to_thread(
        matcher.semantic_matcher.precompute_skills_embeddings,
        semantic_summaries,
    )
    await asyncio.to_thread(matcher.semantic_matcher.embed_query, "warmup")
    return len(summaries), len(cached)


def _check_workers() -> None:
    """Fail-closed when multiple workers detected in production.

    Process-local state (circuit breaker, token bucket, extraction dedup)
    is NOT shared across workers.  In production this is a silent degradation
    (N x configured rate limit, breaker doesn't trip across processes), so we
    refuse to start.  Dev mode only warns.
    """
    import os

    workers = int(os.getenv("UVICORN_WORKERS", "1"))
    if workers <= 1:
        return
    env = os.getenv("ENVIRONMENT", "dev").lower()
    if env in ("dev", "development", "test", "testing"):
        logger.warning(
            "Multi-worker mode (workers=%d) detected in %s environment — "
            "rate limiter and circuit breaker state is process-local. "
            "This is acceptable for local development.",
            workers,
            env,
        )
        return
    logger.critical(
        "Multi-worker mode (workers=%d) is NOT supported in '%s' environment: "
        "circuit breaker / token bucket / extraction dedup state is process-local. "
        "Run with UVICORN_WORKERS=1 or migrate state to Redis.",
        workers,
        env,
    )
    raise SystemExit(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: initialize on startup, cleanup on shutdown."""
    global _critical_startup_error
    _check_workers()
    try:
        from ...core import constants
        from ...core.file_permissions import harden_runtime_permissions

        private_roots = tuple(
            dict.fromkeys(
                (
                    constants.DATA_DIR,
                    settings.DB_PATH.parent,
                    settings.UPLOAD_DIR,
                    settings.VECTOR_DB_DIR,
                    settings.CUSTOM_SKILLS_DIR,
                )
            )
        )
        await asyncio.to_thread(harden_runtime_permissions, *private_roots)
        await asyncio.to_thread(run_alembic_upgrade)
        logger.info("Database initialized at %s", settings.DB_PATH)
        from ...services.rag._publication import recover_persistent_publication

        await asyncio.to_thread(recover_persistent_publication)
        await run_critical_startup()
        _critical_startup_error = None
    except Exception as exc:
        _critical_startup_error = f"{type(exc).__name__}: {exc}"[:200]
        if settings.ENVIRONMENT != "dev":
            # A production process that cannot migrate or initialize required
            # dependencies must never be advertised as a usable instance.
            logger.critical("Critical startup failed; refusing to start", exc_info=True)
            raise
        logger.critical("Critical startup failed; entering degraded mode", exc_info=True)

    suppress_generator_exit_errors(asyncio.get_running_loop())

    # ── Best-effort path: failures are logged but non-fatal ──

    try:

        def _retire_orphans() -> int:
            with get_write_connection() as conn:
                from ...core import database as db

                return db.retire_orphaned_file_jobs(conn)

        retired_jobs = await asyncio.to_thread(_retire_orphans)
        if retired_jobs:
            logger.info("Retired %d dead-letter jobs whose source files were deleted", retired_jobs)
    except Exception:
        record_best_effort_failure("retire_orphaned_file_jobs")
        logger.warning("Orphaned durable-job retirement failed", exc_info=True)

    if settings.DURABLE_JOB_EXECUTION_MODE == "embedded":
        from ...services.jobs import durable_job_worker_loop

        _bg.create("durable_job_worker_loop", durable_job_worker_loop, max_restarts=10)
    else:
        logger.warning("Durable job execution is disabled; queued work will not run")

    from ...services.processor import recover_stale_meetings, resume_interrupted_processing
    from ...services.processor._scheduler import active_processing_file_ids

    try:
        resumed = await resume_interrupted_processing()
        await asyncio.to_thread(
            recover_stale_meetings,
            active_file_ids=active_processing_file_ids(),
        )
        logger.info("Recovered stale meetings; resumed=%d", resumed)
    except Exception:
        record_best_effort_failure("recover_stale_meetings")
        logger.warning("Recover stale meetings failed", exc_info=True)

    _bg.create("stale_recovery_loop", stale_recovery_loop, max_restarts=3)
    _bg.create("bm25_drift_loop", bm25_drift_loop, max_restarts=3)

    try:
        from ...services.chain._meeting_summary_lifecycle import (
            recover_stale_generating_summaries,
        )

        recovered = await asyncio.to_thread(recover_stale_generating_summaries)
        if recovered:
            logger.info("Recovered %d stale generating meeting summaries", recovered)
    except Exception:
        record_best_effort_failure("recover_stale_generating_summaries")
        logger.warning("Stale generating summary recovery failed", exc_info=True)

    try:
        requeued, normalized = await _recover_incomplete_file_summaries()
        if requeued:
            logger.info("Requeueing %d files with incomplete summary_status", requeued)
        if normalized:
            logger.info(
                "Auto-summary is disabled; restored %d parsed files to ready status",
                normalized,
            )
    except Exception:
        record_best_effort_failure("recover_file_summaries")
        logger.warning("File summary recovery failed", exc_info=True)

    try:
        from ...services.chain._meeting_summary_lifecycle import reconcile_meeting_summaries

        reconciled = await asyncio.to_thread(reconcile_meeting_summaries, limit=200)
        if reconciled.get("repaired"):
            logger.info("Meeting summary reconcile: %s", reconciled)
    except Exception:
        record_best_effort_failure("meeting_summary_reconcile")
        logger.warning("Meeting summary reconcile failed", exc_info=True)

    from ...services.memory import (
        _get_active_user_ids,
        _load_session_cache,
        memory_service,
        start_memory_decay_loop,
    )

    try:
        _load_session_cache()
    except Exception:
        record_best_effort_failure("load_session_cache")
        logger.warning("Session cache warm load failed", exc_info=True)

    try:
        from ...services.memory._service._crud import purge_expired_memories_durable

        deleted = await asyncio.to_thread(purge_expired_memories_durable)
        if deleted:
            logger.info("Cleaned up %d expired memories (durable outbox)", deleted)
    except Exception:
        record_best_effort_failure("startup_expired_memory_cleanup")
        logger.warning("Expired memory cleanup skipped", exc_info=True)

    try:
        from ...services.memory._vectorstore import _vector_cb_reset

        for col in ("user_memories", "memory_entities", "session_summaries"):
            _vector_cb_reset(col)
    except Exception:
        logger.debug("Vector store circuit breaker reset skipped", exc_info=True)

    try:
        from ...services.memory._service._crud import cleanup_pending_vector_deletions

        cleaned_vectors = await asyncio.to_thread(cleanup_pending_vector_deletions)
        if cleaned_vectors:
            logger.info("Cleaned up %d orphaned vectors from pending deletions", cleaned_vectors)
    except Exception:
        record_best_effort_failure("pending_vector_deletion_cleanup")
        logger.warning("Pending vector deletion cleanup failed", exc_info=True)

    # Pending fact vectors are durable SQL work. The single background
    # reconciler below drains them without making startup wait for a provider.

    try:
        import time

        upload_dir = settings.UPLOAD_DIR
        stale_secs = 24 * 3600
        now = time.time()
        cleaned = 0
        if upload_dir.exists():
            for entry in upload_dir.iterdir():
                if entry.name.startswith(".upload-") and now - entry.stat().st_mtime > stale_secs:
                    try:
                        entry.unlink()
                        cleaned += 1
                    except OSError:
                        logger.debug("Failed to unlink stale temp file %s", entry, exc_info=True)
        if cleaned:
            logger.info("Cleaned up %d stale upload temp files", cleaned)
    except Exception:
        logger.debug("Stale upload temp file cleanup failed", exc_info=True)

    if settings.MEMORY_DECAY_ENABLED:
        try:
            for uid in _get_active_user_ids():
                memory_service.decay_memories_if_needed(uid)
            logger.info("Startup memory decay complete")
        except Exception:
            record_best_effort_failure("startup_memory_decay")
            logger.warning("Startup memory decay failed", exc_info=True)

    if settings.MEMORY_DECAY_ENABLED:
        _bg.create("memory_decay_loop", start_memory_decay_loop, max_restarts=50)

    _bg.create("expired_memory_purge_loop", expired_purge_loop, max_restarts=3)

    from ...services.memory._service._index_sync import memory_index_reconcile_loop

    _bg.create("memory_index_reconcile_loop", memory_index_reconcile_loop, max_restarts=3)

    try:
        from ...core.database import backfill_chat_messages_fts

        with get_write_connection() as conn:
            filled = backfill_chat_messages_fts(conn)
        if filled:
            logger.info("Backfilled %d chat messages into FTS5 index", filled)
    except Exception:
        record_best_effort_failure("fts5_backfill")
        logger.warning("FTS5 backfill failed (table may not exist yet)", exc_info=True)

    try:
        from ...services.knowledge_graph import kg_service
        from ...services.memory._summary_vectorstore import sync_missing_summary_vectors

        # Both helpers call sync vector-store .upsert, which embeds via the
        # sync embedder; that path refuses to run inside a coroutine. Offload
        # to a worker thread per CLAUDE.md's "blocking calls must be
        # offloaded" rule (matches the file-summary sync just below).
        synced_ent = await asyncio.to_thread(kg_service.sync_missing_entity_vectors)
        synced_sessions = await asyncio.to_thread(sync_missing_summary_vectors)
        if synced_ent or synced_sessions:
            logger.info(
                "Vector sync: %d entities, %d session summaries re-indexed",
                synced_ent,
                synced_sessions,
            )
    except Exception:
        record_best_effort_failure("startup_vector_sync")
        logger.warning("Startup vector sync failed", exc_info=True)

    try:
        from ...services.rag._summary_vectorstore import sync_missing_file_summary_vectors

        synced_files = await asyncio.to_thread(sync_missing_file_summary_vectors)
        if synced_files:
            logger.info("File-summary vector sync: %d backfilled", synced_files)
    except Exception:
        record_best_effort_failure("startup_file_summary_sync")
        logger.warning("Startup file-summary vector sync failed", exc_info=True)

    try:
        from ...services.rag import reconcile_multimodal_index_state

        reconciled = await asyncio.to_thread(reconcile_multimodal_index_state, limit=5000)
        if reconciled.get("reconciled"):
            logger.info("Index-state startup reconciliation completed: %s", reconciled)
    except Exception:
        record_best_effort_failure("startup_index_reconcile")
        logger.warning("Startup index-state reconciliation failed", exc_info=True)

    # M33: Pre-validate all builtin skills at startup so broken skill.md files
    # are caught early (quarantined) rather than at first invocation.
    try:
        from skills.loader import SkillLoader

        _loader = SkillLoader()
        _skills = _loader.load_all()
        logger.info("Loaded %d skill(s) at startup", len(_skills))
    except Exception:
        record_best_effort_failure("startup_skill_validation")
        logger.warning("Skill pre-validation failed", exc_info=True)

    if _startup_summary_backfill_enabled():
        _bg.create("startup_session_summarize", summarize_unsummarized, max_restarts=1)
    elif settings.SESSION_SUMMARY_ENABLED:
        try:
            from ...core import database as db

            with db.get_connection() as conn:
                pending = len(
                    db.get_unsummarized_sessions(
                        conn,
                        user_id=None,
                        min_messages=settings.SESSION_SUMMARY_MIN_TURNS,
                    )
                )
            if pending:
                logger.info(
                    "Startup summary backfill disabled; %d unsummarized sessions pending",
                    pending,
                )
        except Exception:
            record_best_effort_failure("startup_summary_backlog_inspect")
            logger.warning("Failed to inspect startup summary backlog", exc_info=True)

    if settings.SESSION_SUMMARY_ENABLED:
        _bg.create("idle_session_summary_loop", idle_summary_loop, max_restarts=3)

    try:
        from ...services.rag import check_and_rebuild_bm25_if_drifted, rebuild_bm25_from_chroma

        rebuild_bm25_from_chroma()
        check_and_rebuild_bm25_if_drifted()
    except Exception:
        record_best_effort_failure("bm25_rebuild")
        logger.warning("BM25 index rebuild failed", exc_info=True)

    try:
        from ...services.rag._indexer_store import _backfill_legacy_bm25_metadata

        backfilled = await asyncio.to_thread(_backfill_legacy_bm25_metadata)
        if backfilled:
            logger.info("Backfilled file_id/chunk_id in %d legacy BM25 metadata rows", backfilled)
    except Exception:
        logger.debug("BM25 metadata backfill skipped or failed (non-fatal)", exc_info=True)

    _bg.create("wal_checkpoint_loop", wal_checkpoint_loop, max_restarts=3)
    _bg.create("index_reconcile_loop", index_reconcile_loop, max_restarts=3)
    _bg.create("retention_purge_loop", retention_loop, max_restarts=3)

    async def _idempotency_cleanup_loop():
        from ...core.database import get_write_connection
        from ...core.database.idempotency import cleanup_expired_idempotency_keys

        while True:
            await asyncio.sleep(3600)
            try:

                def _cleanup() -> int:
                    from ...services.chat_runs import cleanup_runs

                    cleanup_runs()
                    with get_write_connection() as conn:
                        return cleanup_expired_idempotency_keys(conn)

                deleted = await asyncio.to_thread(_cleanup)
                if deleted:
                    logger.info("Idempotency cleanup: purged %d expired keys", deleted)
                from ...core import database as db

                def _cleanup_jobs() -> int:
                    with db.get_write_connection() as conn:
                        return db.cleanup_finished_jobs(conn, retention_days=7)

                deleted_jobs = await asyncio.to_thread(_cleanup_jobs)
                if deleted_jobs:
                    logger.info("Durable jobs cleanup: purged %d terminal rows", deleted_jobs)
            except Exception:
                logger.warning("Idempotency cleanup failed", exc_info=True)

    _bg.create("idempotency_cleanup_loop", _idempotency_cleanup_loop, max_restarts=3)

    async def _prewarm_skill_matcher():
        try:
            from ...services.chain._api import _get_skill_loader, _get_skill_matcher

            loader = _get_skill_loader()
            matcher = _get_skill_matcher()
            summary_count, precomputed = await _prewarm_skill_matcher_once(loader, matcher)

            logger.info(
                "Skill matcher pre-warmed (summaries=%d, examples_cached=%d)",
                summary_count,
                precomputed,
            )
        except Exception:
            record_best_effort_failure("skill_matcher_pre_warm")
            logger.warning("Skill matcher pre-warm failed (non-fatal)", exc_info=True)

    _bg.create("skill_matcher_prewarm", _prewarm_skill_matcher, max_restarts=1)

    yield

    from ...services.chat_runs import shutdown_runs

    await shutdown_runs()
    await graceful_shutdown()
