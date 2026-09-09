"""Durable job scheduling and embedded execution for the modular monolith.

Producers commit work to SQLite before returning.  Consumers stay in the API
process because SQLite, Chroma persistence and runtime settings are deliberately
single-process resources in this deployment profile.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from typing import Any

from ..core import database as db
from ..core.config import settings
from ..core.database import get_write_connection
from ..core.exceptions import (
    LLMAuthenticationError,
    LLMCircuitBreakerError,
    LLMConfigError,
    LLMContextWindowError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTransientResponseError,
    map_error,
)

logger = logging.getLogger(__name__)

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]

_wake_event: asyncio.Event | None = None
_active_job_tasks: dict[str, tuple[str, str, asyncio.Task[None]]] = {}
_worker_pool_online = False


def _wake() -> None:
    global _wake_event
    if _wake_event is not None:
        _wake_event.set()


def wake_durable_job_workers() -> None:
    """Notify embedded workers after a caller commits a job transaction."""
    _wake()


async def enqueue_durable_job(
    *,
    kind: str,
    dedupe_key: str,
    payload: Mapping[str, Any],
    priority: int = 0,
    max_attempts: int = 3,
) -> str:
    def _persist() -> str:
        with get_write_connection() as conn:
            return db.enqueue_job(
                conn,
                kind=kind,
                dedupe_key=dedupe_key,
                payload=payload,
                priority=priority,
                max_attempts=max_attempts,
            )

    job_id = await asyncio.to_thread(_persist)
    _wake()
    return job_id


async def cancel_durable_jobs(*, kind: str, dedupe_prefix: str, exact: bool = False) -> bool:
    def _cancel() -> list[str]:
        with get_write_connection() as conn:
            return db.cancel_jobs(conn, kind=kind, dedupe_prefix=dedupe_prefix, exact=exact)

    ids = set(await asyncio.to_thread(_cancel))
    cancelled = bool(ids)
    for job_id, (active_kind, active_key, task) in list(_active_job_tasks.items()):
        matches = active_key == dedupe_prefix if exact else active_key.startswith(dedupe_prefix)
        if active_kind == kind and matches:
            ids.add(job_id)
            if not task.done():
                task.cancel()
                cancelled = True
    if ids:
        await asyncio.gather(
            *(entry[2] for jid, entry in list(_active_job_tasks.items()) if jid in ids),
            return_exceptions=True,
        )
    return cancelled


async def _handle_file_processing(payload: dict[str, Any]) -> None:
    from .processor._pipeline import process_meeting_file

    await process_meeting_file(
        int(payload["file_id"]),
        force_meeting_summary=bool(payload.get("force_meeting_summary", False)),
        force_native_reindex=bool(payload.get("force_native_reindex", False)),
        expected_source_revision=(
            int(payload["source_revision"]) if payload.get("source_revision") is not None else None
        ),
    )
    file_id = int(payload["file_id"])
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT status, error_message FROM meeting_files WHERE id=?", (file_id,)
        ).fetchone()
    if row is None:
        # Deletion withdraws the work item. Retrying cannot recreate its source.
        logger.info("File processing retired after source deletion: file=%d", file_id)
        return
    if row["status"] == "error":
        error = row["error_message"] or f"Meeting file {file_id} processing failed"
        if "uploaded file not found" in error.casefold():
            raise FileNotFoundError(error)
        raise RuntimeError(error)


async def _handle_file_summary(payload: dict[str, Any]) -> None:
    from .processor._pipeline_common import run_post_ready_summary

    await run_post_ready_summary(int(payload["file_id"]), int(payload["meeting_id"]))


async def _handle_meeting_summary(payload: dict[str, Any]) -> None:
    from .processor._pipeline_summary import _auto_summarize_meeting

    await _auto_summarize_meeting(int(payload["meeting_id"]))


async def _handle_fact_extraction(payload: dict[str, Any]) -> None:
    from .chain._steps_generate import run_fact_extraction_job

    await run_fact_extraction_job(payload)


async def _handle_speaker_rename(payload: dict[str, Any]) -> None:
    from .speaker_rename import run_speaker_rename_job

    await run_speaker_rename_job(payload)


def _handler_for(kind: str) -> JobHandler:
    handlers: dict[str, JobHandler] = {
        "file_processing": _handle_file_processing,
        "file_summary": _handle_file_summary,
        "meeting_summary": _handle_meeting_summary,
        "fact_extraction": _handle_fact_extraction,
        "speaker_rename": _handle_speaker_rename,
    }
    try:
        return handlers[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown durable job kind: {kind}") from exc


def _retry_policy(exc: Exception, attempts: int, job_id: str) -> tuple[int, bool]:
    """Return a provider-aware retry delay and whether retrying is futile."""
    mapped = map_error(exc)
    if isinstance(mapped, (LLMConfigError, LLMAuthenticationError, LLMContextWindowError)):
        return 0, True
    if isinstance(exc, FileNotFoundError):
        return 0, True

    base = min(300, 2 ** max(0, attempts - 1))
    if isinstance(mapped, LLMCircuitBreakerError):
        base = max(base, int(settings.LLM_CIRCUIT_BREAKER_RECOVERY))
    elif isinstance(mapped, LLMRateLimitError):
        base = max(base, int(mapped.retry_after or 60))
    elif isinstance(mapped, (LLMTimeoutError, LLMTransientResponseError)):
        base = max(base, 30)

    # Stable per-job jitter prevents a recovered provider from receiving every
    # queued retry at exactly the same instant, without making tests flaky.
    jitter_window = max(1, min(30, base // 10))
    jitter = int(hashlib.sha256(job_id.encode()).hexdigest()[:8], 16) % (jitter_window + 1)
    return min(900, base + jitter), False


async def _renew_lease(job_id: str, owner: str, lease_seconds: int) -> None:
    while True:
        await asyncio.sleep(lease_seconds / 3)

        def _renew() -> bool:
            with get_write_connection() as conn:
                return db.renew_job_lease(
                    conn,
                    job_id=job_id,
                    owner=owner,
                    lease_seconds=lease_seconds,
                )

        if not await asyncio.to_thread(_renew):
            return


async def _execute_claimed(job: dict[str, Any], owner: str, lease_seconds: int) -> None:
    job_id = str(job["id"])
    kind = str(job["kind"])
    dedupe_key = str(job["dedupe_key"])
    try:
        payload = json.loads(str(job["payload_json"]))
        if not isinstance(payload, dict):
            raise ValueError("Job payload must be a JSON object")
    except Exception as exc:
        payload = {}
        handler_error: Exception | None = exc
    else:
        handler_error = None

    task: asyncio.Task[None] | None = None
    renewer = asyncio.create_task(
        _renew_lease(job_id, owner, lease_seconds), name=f"job-lease:{job_id}"
    )
    try:
        if handler_error is not None:
            raise handler_error
        handler = _handler_for(kind)
        from ..core.config import activate_settings_snapshot, build_settings_snapshot
        from ..core.settings_epoch import get_settings_epoch

        job_settings = build_settings_snapshot(epoch=get_settings_epoch())

        async def _run_handler() -> None:
            # A job sees one coherent configuration even if the live settings
            # endpoint publishes a new generation while it is running.
            from ..core.job_fence import activate_job_fence

            with activate_settings_snapshot(job_settings), activate_job_fence(job_id, owner):
                await handler(payload)

        task = asyncio.create_task(_run_handler(), name=f"job:{kind}:{job_id}")
        _active_job_tasks[job_id] = (kind, dedupe_key, task)
        done, _pending = await asyncio.wait({task, renewer}, return_when=asyncio.FIRST_COMPLETED)
        if task not in done:
            # The renewer only exits while work is active when ownership was
            # lost or lease renewal failed. Stop the stale executor before it
            # can continue committing additional side effects.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            lease_error = renewer.exception()
            if lease_error is not None:
                raise RuntimeError("Durable job lease renewal failed") from lease_error
            raise RuntimeError("Durable job lease ownership was lost")
        await task

        def _complete() -> bool:
            with get_write_connection() as conn:
                return db.complete_job(conn, job_id=job_id, owner=owner)

        if not await asyncio.to_thread(_complete):
            logger.info("Job %s completed after its lease was cancelled or reassigned", job_id)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        executor_cancelled = bool(current and current.cancelling())
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        def _release() -> bool:
            with get_write_connection() as conn:
                return db.release_job(conn, job_id=job_id, owner=owner)

        await asyncio.to_thread(_release)
        if executor_cancelled:
            raise
        # A delete/cancel request withdraws only the active handler task.  It
        # must not cancel the worker coroutine that awaited that handler,
        # otherwise asyncio.gather tears down the entire embedded worker pool.
        logger.info("Durable job %s (%s) stopped after cancellation", job_id, kind)
        return
    except Exception as exc:
        attempts = int(job.get("attempts", 1))
        delay, terminal = _retry_policy(exc, attempts, job_id)
        error = f"{type(exc).__name__}: {exc}"

        def _fail() -> str | None:
            with get_write_connection() as conn:
                return db.fail_job(
                    conn,
                    job_id=job_id,
                    owner=owner,
                    error=error,
                    retry_delay_seconds=delay,
                    force_terminal=terminal,
                )

        state = await asyncio.to_thread(_fail)
        logger.error("Durable job %s (%s) failed; state=%s", job_id, kind, state, exc_info=True)
    finally:
        renewer.cancel()
        await asyncio.gather(renewer, return_exceptions=True)
        _active_job_tasks.pop(job_id, None)


async def _worker(slot: int) -> None:
    import os

    while True:
        owner = f"{socket.gethostname()}:{os.getpid()}:{slot}:{uuid.uuid4().hex}"
        lease_seconds = max(1, int(settings.DURABLE_JOB_LEASE_SECONDS))

        def _claim(
            *, lease_seconds: int = lease_seconds, owner: str = owner
        ) -> dict[str, Any] | None:
            with get_write_connection() as conn:
                return db.claim_next_job(
                    conn,
                    owner=owner,
                    lease_seconds=lease_seconds,
                )

        job = await asyncio.to_thread(_claim)
        if job is not None:
            await _execute_claimed(job, owner, lease_seconds)
            continue
        assert _wake_event is not None
        _wake_event.clear()
        with suppress(TimeoutError):
            await asyncio.wait_for(
                _wake_event.wait(), timeout=max(0.05, float(settings.DURABLE_JOB_POLL_SECONDS))
            )


async def durable_job_worker_loop() -> None:
    """Run the configured number of durable-job consumers until cancelled."""
    global _wake_event, _worker_pool_online
    _wake_event = asyncio.Event()
    workers = [
        asyncio.create_task(_worker(slot), name=f"durable-job-worker:{slot}")
        for slot in range(max(1, int(settings.DURABLE_JOB_WORKERS)))
    ]
    _worker_pool_online = True
    logger.info("Started %d durable job worker(s)", len(workers))
    try:
        await asyncio.gather(*workers)
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        _wake_event = None
        _worker_pool_online = False


def durable_job_workers_online() -> bool:
    return _worker_pool_online


def durable_job_snapshot() -> dict[str, int]:
    with db.get_connection() as conn:
        counts = db.job_health_stats(conn)
    counts["active"] = len(_active_job_tasks)
    return counts
