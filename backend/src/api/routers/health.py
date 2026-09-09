"""Health check API - liveness, readiness, and traffic state endpoints."""

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request, Response
from fastapi import status as http_status
from pydantic import BaseModel, Field

from ...api.middleware import limiter
from ...core import database as db
from ...core.config import settings
from ...core.security import verify_api_key
from ...core.tracing import otel_span

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

# Cache for expensive checks (embeddings, vectorstore) — TTL in seconds
_EMBEDDING_CHECK_TTL = 60.0
_EMBEDDING_CACHE: tuple[float, tuple[str, str]] = (0.0, ("unknown", ""))
_EMBEDDING_CACHE_LOCK = threading.Lock()

_VECTORSTORE_CHECK_TTL = 60.0
_VECTORSTORE_CACHE: tuple[float, tuple[str, str]] = (0.0, ("unknown", ""))
_VECTORSTORE_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str]
    details: dict[str, str] = Field(default_factory=dict)


class LivenessResponse(BaseModel):
    status: str = "alive"


class TrafficHealthResponse(BaseModel):
    breaker_state: str = Field(description="closed | open | half-open")
    open_since: float | None = Field(None, description="Monotonic timestamp when breaker opened")
    error_rate_5m: float = Field(0.0, description="Error rate over the last 60s (0.0-1.0)")
    tokens_available: float = Field(0.0, description="Current rate-limit token count")
    inflight: int = Field(0, description="Current in-flight LLM requests")


class IndexConsistencyResponse(BaseModel):
    status: str
    raganything_enabled: bool
    total_ready_files: int
    missing_chroma_indexed: int
    missing_bm25_indexed: int
    failed_native_indexes: int
    repair_pending_indexes: int
    config_manifest_mismatches: int
    missing_raganything_doc_id: int
    stale_raganything_doc_id: int


class JobHealthResponse(BaseModel):
    status: str
    execution_mode: str
    workers_online: bool
    counts: dict[str, int]


class CapabilityHealthResponse(BaseModel):
    status: str
    capabilities: dict[str, str]
    details: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Component checkers
# ---------------------------------------------------------------------------


def _check_database() -> tuple[str, str]:
    """Check database connectivity. Returns (status, detail)."""
    try:
        with db.get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return ("ok", "")
    except Exception as exc:
        return ("error", str(exc)[:200])


def _check_fts5() -> tuple[str, str]:
    """Check that both FTS tables are queryable without mutating indexes."""
    try:
        with db.get_connection() as conn:
            conn.execute("SELECT rowid FROM bm25_chunks LIMIT 1").fetchone()
            conn.execute("SELECT rowid FROM bm25_chunks_cjk LIMIT 1").fetchone()
        return ("ok", "")
    except Exception as exc:
        logger.warning("FTS5 health check failed", exc_info=True)
        return ("error", str(exc)[:200])


def _check_job_queue() -> tuple[str, str]:
    """Verify the durable queue schema is queryable without mutating it."""
    try:
        with db.get_connection() as conn:
            conn.execute("SELECT status FROM durable_jobs LIMIT 1").fetchone()
        return ("ok", "")
    except Exception as exc:
        return ("error", str(exc)[:200])


def _check_storage() -> tuple[str, str]:
    """Verify configured storage roots exist and are accessible."""
    paths = (settings.UPLOAD_DIR, settings.VECTOR_DB_DIR)
    unavailable = [
        str(path)
        for path in paths
        if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK)
    ]
    if unavailable:
        return ("error", "unavailable: " + ", ".join(unavailable)[:180])
    return ("ok", "")


def _check_llm() -> tuple[str, str]:
    """Check LLM singleton availability. Returns (status, detail)."""
    try:
        from ...services.llm import get_llm

        llm = get_llm()
        return ("ok", llm.__class__.__name__) if llm else ("error", "LLM singleton is None")
    except Exception as exc:
        return ("error", str(exc)[:200])


def _check_embeddings() -> tuple[str, str]:
    """Check embeddings connectivity with a tiny embed call. Returns (status, detail).

    Cached for 60s to avoid hitting the API on every health poll.
    """
    global _EMBEDDING_CACHE
    now = time.monotonic()
    with _EMBEDDING_CACHE_LOCK:
        cached_time, cached_result = _EMBEDDING_CACHE
        if now - cached_time < _EMBEDDING_CHECK_TTL:
            return cached_result

    try:
        from ...services.embedder import get_embeddings

        embeddings = get_embeddings()
        vec = embeddings.embed_query("ping")
        result = ("ok", f"dim={len(vec)}") if vec else ("error", "empty embedding")
    except Exception as exc:
        result = ("error", str(exc)[:200])

    with _EMBEDDING_CACHE_LOCK:
        _EMBEDDING_CACHE = (now, result)
    return result


def _check_vectorstore() -> tuple[str, str]:
    """Check Chroma vector store. Returns (status, detail).

    Cached for 60s to avoid expensive collection count on every health poll.
    """
    global _VECTORSTORE_CACHE
    now = time.monotonic()
    with _VECTORSTORE_CACHE_LOCK:
        cached_time, cached_result = _VECTORSTORE_CACHE
        if now - cached_time < _VECTORSTORE_CHECK_TTL:
            return cached_result

    try:
        from ...services.rag import get_vectorstore

        vs = get_vectorstore()
        count = vs._collection.count()
        result = ("ok", f"chunks={count}")
    except Exception as exc:
        result = ("error", str(exc)[:200])

    with _VECTORSTORE_CACHE_LOCK:
        _VECTORSTORE_CACHE = (now, result)
    return result


def _check_critical_startup() -> tuple[str, str]:
    """Expose failures captured while entering the application lifespan."""
    from ..lifespan import get_critical_startup_error

    error = get_critical_startup_error()
    return ("error", error) if error else ("ok", "")


def _check_index_consistency() -> IndexConsistencyResponse:
    """Check consistency between ready files and index_state metadata."""
    with db.get_connection() as conn:
        total_ready_files = int(
            conn.execute("SELECT COUNT(*) FROM meeting_files WHERE status='ready'").fetchone()[0]
        )
        missing_chroma = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM meeting_files mf
                LEFT JOIN index_state ist ON ist.file_id = mf.id
                WHERE mf.status='ready'
                  AND (ist.chroma_indexed_at IS NULL OR ist.chroma_indexed_at='')
                """
            ).fetchone()[0]
        )
        missing_bm25 = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM meeting_files mf
                LEFT JOIN index_state ist ON ist.file_id = mf.id
                WHERE mf.status='ready'
                  AND (ist.bm25_indexed_at IS NULL OR ist.bm25_indexed_at='')
                """
            ).fetchone()[0]
        )
        failed_native = int(
            conn.execute(
                "SELECT COUNT(*) FROM index_state ist "
                "JOIN meeting_files mf ON mf.id=ist.file_id "
                "WHERE mf.status='ready' AND ist.native_status='failed'"
            ).fetchone()[0]
        )
        repair_pending = int(
            conn.execute(
                "SELECT COUNT(*) FROM index_state ist "
                "JOIN meeting_files mf ON mf.id=ist.file_id "
                "WHERE mf.status='ready' AND ist.repair_pending=1"
            ).fetchone()[0]
        )
        from ...core.index_manifest import index_config_fingerprint

        config_mismatches = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM meeting_files mf
                LEFT JOIN index_state ist ON ist.file_id=mf.id
                WHERE mf.status='ready'
                  AND COALESCE(ist.native_config_fingerprint, '') != ?
                """,
                (index_config_fingerprint(),),
            ).fetchone()[0]
        )
        missing = (
            int(
                conn.execute(
                    """
                SELECT COUNT(*)
                FROM meeting_files mf
                LEFT JOIN index_state ist ON ist.file_id = mf.id
                WHERE mf.status='ready'
                  AND (ist.raganything_doc_id IS NULL OR ist.raganything_doc_id='')
                """
                ).fetchone()[0]
            )
            if settings.RAGANYTHING_ENABLED
            else 0
        )
        stale = (
            int(
                conn.execute(
                    """
                SELECT COUNT(*)
                FROM meeting_files mf
                LEFT JOIN index_state ist ON ist.file_id = mf.id
                WHERE mf.status='ready'
                  AND ist.raganything_doc_id IS NOT NULL
                  AND ist.raganything_doc_id != ''
                  AND ist.raganything_doc_id != ('meeting_' || mf.meeting_id || '_file_' || mf.id)
                """
                ).fetchone()[0]
            )
            if settings.RAGANYTHING_ENABLED
            else 0
        )

    status = (
        "ok"
        if missing_chroma == 0
        and missing_bm25 == 0
        and failed_native == 0
        and repair_pending == 0
        and config_mismatches == 0
        and missing == 0
        and stale == 0
        else "degraded"
    )
    return IndexConsistencyResponse(
        status=status,
        raganything_enabled=settings.RAGANYTHING_ENABLED,
        total_ready_files=total_ready_files,
        missing_chroma_indexed=missing_chroma,
        missing_bm25_indexed=missing_bm25,
        failed_native_indexes=failed_native,
        repair_pending_indexes=repair_pending,
        config_manifest_mismatches=config_mismatches,
        missing_raganything_doc_id=missing,
        stale_raganything_doc_id=stale,
    )


def _check_durable_jobs() -> JobHealthResponse:
    from ...services.jobs import durable_job_workers_online

    with db.get_connection() as conn:
        counts = db.job_health_stats(conn)
        lifecycle_rows = conn.execute(
            "SELECT lifecycle_state,COUNT(*) AS total,"
            "SUM(CASE WHEN expires_at<=CURRENT_TIMESTAMP THEN 1 ELSE 0 END) AS expired "
            "FROM idempotency_keys GROUP BY lifecycle_state"
        ).fetchall()
    for row in lifecycle_rows:
        state = str(row["lifecycle_state"] or "legacy_unknown")
        counts[f"idempotency_{state}"] = int(row["total"] or 0)
        counts[f"idempotency_{state}_expired"] = int(row["expired"] or 0)
    workers_online = durable_job_workers_online()
    ambiguous_idempotency = counts.get("idempotency_effects_committed", 0) + counts.get(
        "idempotency_legacy_unknown", 0
    )
    status = "degraded" if counts.get("dead_letter", 0) or ambiguous_idempotency else "ok"
    if settings.DURABLE_JOB_EXECUTION_MODE == "off" and counts.get("pending", 0):
        status = "degraded"
    if (
        settings.DURABLE_JOB_EXECUTION_MODE == "embedded"
        and counts.get("pending", 0)
        and not workers_online
    ):
        status = "degraded"
    return JobHealthResponse(
        status=status,
        execution_mode=settings.DURABLE_JOB_EXECUTION_MODE,
        workers_online=workers_online,
        counts=counts,
    )


def _check_job_execution() -> tuple[str, str]:
    state = _check_durable_jobs()
    pending = state.counts.get("pending", 0)
    expired_running = state.counts.get("expired_running", 0)
    # Dead letters belong on the authenticated job-health dashboard but do
    # not make the API pod unready: restarting cannot repair terminal work.
    if pending == 0 and expired_running == 0:
        return ("ok", "")
    if state.execution_mode == "embedded" and state.workers_online:
        return ("ok", "")
    return (
        "error",
        f"mode={state.execution_mode}, workers_online={state.workers_online}, "
        f"counts={state.counts}",
    )


def _check_native_index_readiness() -> tuple[str, str]:
    """Reject traffic while ready files lack a verified active manifest."""
    state = _check_index_consistency()
    native_issues = {
        "missing_chroma": state.missing_chroma_indexed,
        "missing_bm25": state.missing_bm25_indexed,
        "failed": state.failed_native_indexes,
        "repair_pending": state.repair_pending_indexes,
        "config_mismatch": state.config_manifest_mismatches,
    }
    if all(value == 0 for value in native_issues.values()):
        return ("ok", "")
    detail = ", ".join(f"{name}={value}" for name, value in native_issues.items() if value)
    return ("error", detail)


# ---------------------------------------------------------------------------
# Readiness checker — parallel component checks with timeout
# ---------------------------------------------------------------------------


async def _run_check_with_timeout(
    name: str,
    checker: Callable[[], tuple[str, str]],
    timeout: float = 2.0,
) -> tuple[str, str, str]:
    """Run a single component check with a timeout guard."""
    try:
        result = await asyncio.wait_for(asyncio.to_thread(checker), timeout=timeout)
        return (name, result[0], result[1])
    except TimeoutError:
        return (name, "timeout", f"exceeded {timeout}s")
    except Exception as exc:
        return (name, "error", str(exc)[:200])


async def _check_readiness() -> HealthResponse:
    """Run local, read-only readiness checks with individual timeouts.

    Readiness is polled frequently by orchestrators. It must never call paid
    providers or repair indexes.
    """
    checks_to_run = [
        ("startup", _check_critical_startup),
        ("database", _check_database),
        ("fts5", _check_fts5),
        ("job_queue", _check_job_queue),
        ("job_execution", _check_job_execution),
        ("native_index", _check_native_index_readiness),
        ("storage", _check_storage),
    ]

    tasks = [_run_check_with_timeout(name, fn, timeout=2.0) for name, fn in checks_to_run]
    results = await asyncio.gather(*tasks)

    statuses: dict[str, str] = {}
    details: dict[str, str] = {}
    for name, status, detail in results:
        statuses[name] = status
        if detail:
            details[name] = detail

    overall = "ok" if all(v == "ok" for v in statuses.values()) else "degraded"
    return HealthResponse(status=overall, checks=statuses, details=details)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health/live", response_model=LivenessResponse)
@limiter.exempt
async def liveness() -> LivenessResponse:
    """Liveness probe — always returns 200 if the process is alive."""
    return LivenessResponse()


@router.get("/health/ready", response_model=HealthResponse)
@limiter.exempt
async def readiness(response: Response) -> HealthResponse:
    """Readiness probe — local checks only, with no paid provider calls."""
    result = await _check_readiness()
    if result.status != "ok":
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
        response.headers["Retry-After"] = "5"
    return result


@router.get("/health/traffic", response_model=TrafficHealthResponse)
@limiter.limit("30/minute")
async def traffic_health(
    request: Request,
    principal: dict = Depends(verify_api_key),
) -> TrafficHealthResponse:
    """Expose circuit breaker state and rate-limit status for ops dashboards."""
    from ...services.traffic_control import traffic_controller

    if traffic_controller is None:
        return TrafficHealthResponse(
            breaker_state="uninitialized",
            open_since=None,
            error_rate_5m=0.0,
            tokens_available=0.0,
            inflight=0,
        )

    breaker = traffic_controller.breaker
    tracker = traffic_controller.tracker

    # Extract breaker open timestamp if available
    open_since = None
    if breaker.state == "open":
        open_since = getattr(breaker, "_last_failure_time", None)

    # Current token count (approximate, lock-free read)
    tokens = getattr(traffic_controller, "_tokens", 0.0)

    # In-flight count: semaphore capacity minus available permits
    max_conc = traffic_controller._max_concurrency
    # _semaphore._value is an internal detail but the standard way to read it
    inflight = max_conc - getattr(traffic_controller._semaphore, "_value", max_conc)

    return TrafficHealthResponse(
        breaker_state=breaker.state,
        open_since=open_since,
        error_rate_5m=round(tracker.error_rate, 3),
        tokens_available=round(tokens, 1),
        inflight=inflight,
    )


@router.get("/health", response_model=HealthResponse)
@limiter.exempt
async def health(response: Response) -> HealthResponse:
    """Legacy health check — delegates to readiness for backward compatibility."""
    with otel_span("api.health"):
        result = await _check_readiness()
        if result.status != "ok":
            response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
            response.headers["Retry-After"] = "5"
        return result


@router.get("/health/index-consistency", response_model=IndexConsistencyResponse)
@limiter.limit("30/minute")
async def health_index_consistency(
    request: Request,
    principal: dict = Depends(verify_api_key),
) -> IndexConsistencyResponse:
    """Report consistency status between ready files and multimodal index doc IDs."""
    return await asyncio.to_thread(_check_index_consistency)


@router.get("/health/jobs", response_model=JobHealthResponse)
@limiter.limit("30/minute")
async def health_jobs(
    request: Request,
    principal: dict = Depends(verify_api_key),
) -> JobHealthResponse:
    """Report durable queue backlog, dead letters, and idempotency recovery state."""
    return await asyncio.to_thread(_check_durable_jobs)


@router.get("/health/capabilities", response_model=CapabilityHealthResponse)
@limiter.limit("10/minute")
async def health_capabilities(
    request: Request,
    principal: dict = Depends(verify_api_key),
) -> CapabilityHealthResponse:
    """Probe optional AI capabilities without changing process readiness."""
    checks = [
        _run_check_with_timeout("llm", _check_llm, timeout=5.0),
        _run_check_with_timeout("embeddings", _check_embeddings, timeout=35.0),
        _run_check_with_timeout("vectorstore", _check_vectorstore, timeout=5.0),
    ]
    results = await asyncio.gather(*checks)
    capabilities = {name: status for name, status, _detail in results}
    details = {name: detail for name, _status, detail in results if detail}
    overall = "ok" if all(value == "ok" for value in capabilities.values()) else "degraded"
    return CapabilityHealthResponse(
        status=overall,
        capabilities=capabilities,
        details=details,
    )


@router.post("/health/reset-memory-cb")
@limiter.limit("5/minute")
async def reset_memory_circuit_breaker(
    request: Request,
    principal: dict = Depends(verify_api_key),
) -> dict[str, str]:
    """H-MEM-5: Reset memory vectorstore circuit breaker at runtime.

    Allows operators to clear a stuck circuit breaker without restarting
    the process, e.g. after Chroma recovers from an outage.
    """
    from ...services.memory._vectorstore import _vector_cb_lock, _vector_cb_state

    with _vector_cb_lock:
        count = len(_vector_cb_state)
        _vector_cb_state.clear()
    logger.info("Memory vector circuit breaker reset (cleared %d entries)", count)
    return {"status": "ok", "cleared_entries": str(count)}
