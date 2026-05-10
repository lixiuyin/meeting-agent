"""Health check API - liveness, readiness, and traffic state endpoints."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...api.middleware import limiter
from ...core import database as db
from ...core.config import settings
from ...core.database.bm25 import check_fts5_integrity
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
    missing_raganything_doc_id: int
    stale_raganything_doc_id: int


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
    """Check FTS5 index integrity. Returns (status, detail).

    Uses write connection because FTS5 integrity-check is a write operation
    on external content tables.
    """
    try:
        with db.get_write_connection() as conn:
            healthy = check_fts5_integrity(conn)
        if healthy:
            return ("ok", "")
        logger.warning("FTS5 integrity check failed; triggering rebuild from Chroma")
        from ...services.rag import rebuild_bm25_from_chroma

        rebuild_bm25_from_chroma()
        with db.get_write_connection() as conn:
            healthy = check_fts5_integrity(conn)
        return ("ok" if healthy else "degraded", "rebuilt from Chroma")
    except Exception as exc:
        logger.warning("FTS5 health check failed", exc_info=True)
        return ("error", str(exc)[:200])


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


def _check_index_consistency() -> IndexConsistencyResponse:
    """Check consistency between ready files and index_state metadata."""
    if not settings.RAGANYTHING_ENABLED:
        return IndexConsistencyResponse(
            status="skipped",
            raganything_enabled=False,
            total_ready_files=0,
            missing_chroma_indexed=0,
            missing_raganything_doc_id=0,
            stale_raganything_doc_id=0,
        )

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
        missing = int(
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
        stale = int(
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

    status = "ok" if missing_chroma == 0 and missing == 0 and stale == 0 else "degraded"
    return IndexConsistencyResponse(
        status=status,
        raganything_enabled=True,
        total_ready_files=total_ready_files,
        missing_chroma_indexed=missing_chroma,
        missing_raganything_doc_id=missing,
        stale_raganything_doc_id=stale,
    )


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
    """Run all readiness checks in parallel with individual timeouts."""
    checks_to_run = [
        ("database", _check_database),
        ("fts5", _check_fts5),
        ("llm", _check_llm),
        ("embeddings", _check_embeddings),
        ("vectorstore", _check_vectorstore),
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
async def readiness() -> HealthResponse:
    """Readiness probe — checks DB, LLM, embeddings, vectorstore, FTS5."""
    return await _check_readiness()


@router.get("/health/traffic", response_model=TrafficHealthResponse)
@limiter.exempt
async def traffic_health() -> TrafficHealthResponse:
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
async def health() -> HealthResponse:
    """Legacy health check — delegates to readiness for backward compatibility."""
    with otel_span("api.health"):
        return await _check_readiness()


@router.get("/health/index-consistency", response_model=IndexConsistencyResponse)
@limiter.limit("30/minute")
async def health_index_consistency(
    request: Request,
    principal: dict = Depends(verify_api_key),
) -> IndexConsistencyResponse:
    """Report consistency status between ready files and multimodal index doc IDs."""
    return await asyncio.to_thread(_check_index_consistency)


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
