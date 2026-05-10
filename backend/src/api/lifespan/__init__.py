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


def _startup_summary_backfill_enabled() -> bool:
    return settings.SESSION_SUMMARY_ENABLED and settings.SESSION_SUMMARY_STARTUP_BACKFILL


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
    _check_workers()
    try:
        await asyncio.to_thread(run_alembic_upgrade)
        logger.info("Database initialized at %s", settings.DB_PATH)
        await run_critical_startup()
    except Exception:
        logger.critical("Critical startup failed; entering degraded mode", exc_info=True)

    suppress_generator_exit_errors(asyncio.get_running_loop())

    # ── Best-effort path: failures are logged but non-fatal ──

    from ...services.processor import recover_stale_meetings

    try:
        await asyncio.to_thread(recover_stale_meetings)
        logger.info("Recovered stale meetings")
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
        from ...core.database import get_connection
        from ...services.processor._pipeline_common import schedule_post_ready_summary

        def _recover_file_summaries():
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, meeting_id FROM meeting_files "
                    "WHERE (status='summarizing' OR "
                    "(status='ready' AND summary_status NOT IN ('ready','failed')))"
                ).fetchall()
            return [dict(r) for r in rows]

        stale_files = await asyncio.to_thread(_recover_file_summaries)
        if stale_files:
            logger.info("Requeueing %d files with incomplete summary_status", len(stale_files))
            for f in stale_files:
                await schedule_post_ready_summary(f["id"], f["meeting_id"])
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

    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(settings.VECTOR_DB_DIR))
        collections = [c.name for c in client.list_collections()]

        retired = "meetings_retired"
        if retired in collections and "meetings" not in collections:
            logger.warning(
                "Detected aborted rebuild swap — rolling back 'meetings_retired' to 'meetings'"
            )
            client.rename_collection(retired, "meetings")  # type: ignore[attr-defined]
        elif retired in collections and "meetings" in collections:
            logger.info("Dropping stale retired collection from previous rebuild")
            client.delete_collection(retired)

        for name in collections:
            if name.startswith("meetings_shadow_"):
                logger.info("Dropping orphaned shadow collection '%s'", name)
                client.delete_collection(name)
    except Exception:
        record_best_effort_failure("rebuild_swap_reconcile")
        logger.warning("Rebuild swap reconciliation failed", exc_info=True)

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

    from ...core.database import (
        delete_expired_memories,
        get_expired_memory_ids,
        get_write_connection,
    )

    try:
        with get_write_connection() as conn:
            expired = get_expired_memory_ids(conn)
            if expired:
                try:
                    from ...services.memory import get_memory_vectorstore

                    vs = get_memory_vectorstore()
                    for m in expired:
                        eid = m.get("embedding_id")
                        if eid:
                            try:
                                vs.delete(eid)
                            except Exception:
                                record_best_effort_failure("startup_expired_vector_delete")
                                logger.warning("Failed to delete vector %s", eid, exc_info=True)
                except Exception:
                    record_best_effort_failure("startup_expired_vector_cleanup")
                    logger.warning("Failed to clean up expired memory vectors", exc_info=True)
            deleted = delete_expired_memories(conn)
            if deleted:
                logger.info("Cleaned up %d expired memories (SQLite + Chroma)", deleted)
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

    try:
        from ...services.memory._service._crud import requeue_pending_memory_vectors

        requeued = await asyncio.to_thread(requeue_pending_memory_vectors)
        if requeued:
            logger.info("Re-queued %d zombie pending memory vectors", requeued)
    except Exception:
        record_best_effort_failure("pending_memory_vector_requeue")
        logger.warning("Pending memory vector requeue failed", exc_info=True)

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

        # Both helpers call sync vector-store .upsert, which embeds via the
        # sync embedder; that path refuses to run inside a coroutine. Offload
        # to a worker thread per CLAUDE.md's "blocking calls must be
        # offloaded" rule (matches the file-summary sync just below).
        synced_mem = await asyncio.to_thread(memory_service.sync_missing_vectors)
        synced_ent = await asyncio.to_thread(kg_service.sync_missing_entity_vectors)
        if synced_mem or synced_ent:
            logger.info("Vector sync: %d memories, %d entities re-indexed", synced_mem, synced_ent)
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
            logger.info("Backfilled chunk_id in %d legacy BM25 metadata rows", backfilled)
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
                with get_write_connection() as conn:
                    deleted = cleanup_expired_idempotency_keys(conn)
                if deleted:
                    logger.info("Idempotency cleanup: purged %d expired keys", deleted)
            except Exception:
                logger.warning("Idempotency cleanup failed", exc_info=True)

    _bg.create("idempotency_cleanup_loop", _idempotency_cleanup_loop, max_restarts=3)

    async def _prewarm_skill_matcher():
        try:
            from ...services.chain._api import _get_skill_loader, _get_skill_matcher

            loader = _get_skill_loader()
            matcher = _get_skill_matcher()
            summaries = loader.load_summaries() if hasattr(loader, "load_summaries") else []

            await asyncio.to_thread(matcher.semantic_matcher.embed_query, "warmup")

            precomputed = 0
            for summary in summaries:
                config = getattr(summary, "intent_matching", None)
                if not config or config.method not in ("semantic", "hybrid"):
                    continue
                if not getattr(config, "examples", None):
                    continue
                try:
                    ok = await asyncio.to_thread(
                        matcher.semantic_matcher.precompute_skill_embeddings, summary
                    )
                    if ok:
                        precomputed += 1
                except Exception:
                    logger.warning(
                        "Skill embedding pre-compute failed for %s",
                        summary.name,
                        exc_info=True,
                    )

            logger.info(
                "Skill matcher pre-warmed (summaries=%d, examples_cached=%d)",
                len(summaries),
                precomputed,
            )
        except Exception:
            record_best_effort_failure("skill_matcher_pre_warm")
            logger.warning("Skill matcher pre-warm failed (non-fatal)", exc_info=True)

    _bg.create("skill_matcher_prewarm", _prewarm_skill_matcher, max_restarts=1)

    yield

    await graceful_shutdown()
