"""Settings router shared state and router instance."""

import asyncio
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException

from ....core.config import settings
from ....core.security import verify_api_key

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(verify_api_key)])
logger = logging.getLogger(__name__)
_settings_lock = threading.Lock()


def validate_settings_invariants(
    top_k: int | None = None,
    reranker_top_n: int | None = None,
    hybrid_alpha: float | None = None,
    rag_retriever_provider: str | None = None,
) -> None:
    """Validate cross-field invariants that must hold after any settings change.

    Called before applying in-memory updates so the request is rejected
    before any singleton is reset or epoch bumped.
    """
    effective_top_k = top_k if top_k is not None else settings.TOP_K
    effective_reranker_top_n = (
        reranker_top_n if reranker_top_n is not None else settings.RERANKER_TOP_N
    )
    effective_alpha = hybrid_alpha if hybrid_alpha is not None else settings.HYBRID_ALPHA
    effective_provider = (
        _normalize_retriever_provider(rag_retriever_provider)
        if rag_retriever_provider is not None
        else _normalize_retriever_provider(settings.RAG_RETRIEVER_PROVIDER)
    )

    errors: list[str] = []

    if effective_reranker_top_n < effective_top_k:
        errors.append(
            f"RERANKER_TOP_N ({effective_reranker_top_n}) must be >= TOP_K ({effective_top_k})"
        )

    if not (0.0 <= effective_alpha <= 1.0):
        errors.append(f"HYBRID_ALPHA ({effective_alpha}) must be between 0 and 1")

    if effective_provider == "hybrid" and not settings.HYBRID_SEARCH_ENABLED:
        errors.append("retriever_provider 'hybrid' requires HYBRID_SEARCH_ENABLED=true")

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))


def _normalize_retriever_provider(value: str) -> str:
    mode = value.strip().lower()
    if mode in {"native", "hybrid", "multimodal", "hybrid_multimodal"}:
        return mode
    return "native"


_active_rebuild_tasks: set[asyncio.Task] = set()


class _RebuildState:
    """Single global flag for all index rebuild operations.

    Vector and multimodal rebuilds both write to Chroma; allowing them to
    run concurrently risks interleaved delete+upsert sequences that corrupt
    index state (C-C4). A single lock serializes all rebuilds.
    """

    active: bool = False

    # Backwards-compatible property aliases so existing code still works.
    @property
    def vectors(self) -> bool:
        return self.active

    @vectors.setter
    def vectors(self, value: bool) -> None:
        self.active = value

    @property
    def multimodal(self) -> bool:
        return self.active

    @multimodal.setter
    def multimodal(self, value: bool) -> None:
        self.active = value


rebuild_state = _RebuildState()


def _try_acquire_db_advisory_lock(lock_name: str, timeout_seconds: int = 0) -> bool:
    """Try to acquire a DB-based advisory lock for multi-worker deployments.

    Uses the index_state table to persist lock state so workers on different
    processes can coordinate. Falls back to in-process check only when DB
    is unavailable.
    """
    from ....core.database import get_write_connection

    try:
        with get_write_connection() as conn:
            row = conn.execute("SELECT value FROM index_state WHERE key=?", (lock_name,)).fetchone()
            if row and row["value"] == "locked":
                import time

                locked_at = conn.execute(
                    "SELECT value FROM index_state WHERE key=?",
                    (f"{lock_name}_at",),
                ).fetchone()
                # Auto-expire stale locks after 30 minutes
                if locked_at:
                    try:
                        lock_time = float(locked_at["value"])
                        if time.time() - lock_time > 1800:
                            conn.execute(
                                "UPDATE index_state SET value=? WHERE key=?",
                                ("expired", lock_name),
                            )
                        else:
                            return False
                    except (ValueError, TypeError):
                        pass
                else:
                    return False
    except Exception:
        logger.warning("DB advisory lock check failed for %s", lock_name, exc_info=True)

    return True


def _set_db_advisory_lock(lock_name: str) -> None:
    """Persist lock state in DB for multi-worker coordination."""
    import time

    from ....core.database import get_write_connection

    try:
        with get_write_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO index_state (key, value) VALUES (?, ?)",
                (lock_name, "locked"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO index_state (key, value) VALUES (?, ?)",
                (f"{lock_name}_at", str(time.time())),
            )
    except Exception:
        logger.warning("DB advisory lock set failed for %s", lock_name, exc_info=True)


def _release_db_advisory_lock(lock_name: str) -> None:
    """Release the DB-based advisory lock."""
    from ....core.database import get_write_connection

    try:
        with get_write_connection() as conn:
            conn.execute("DELETE FROM index_state WHERE key=?", (lock_name,))
            conn.execute("DELETE FROM index_state WHERE key=?", (f"{lock_name}_at",))
    except Exception:
        logger.warning("DB advisory lock release failed for %s", lock_name, exc_info=True)


def try_acquire_vectors_rebuild() -> bool:
    """Atomically claim the rebuild slot. Returns False if any rebuild is in progress."""
    with _settings_lock:
        if rebuild_state.active:
            return False
        if not _try_acquire_db_advisory_lock("rebuild_vectors"):
            return False
        rebuild_state.active = True
        _set_db_advisory_lock("rebuild_vectors")
        return True


def try_acquire_multimodal_rebuild() -> bool:
    """Atomically claim the rebuild slot. Returns False if any rebuild is in progress."""
    with _settings_lock:
        if rebuild_state.active:
            return False
        if not _try_acquire_db_advisory_lock("rebuild_multimodal"):
            return False
        rebuild_state.active = True
        _set_db_advisory_lock("rebuild_multimodal")
        return True


def release_all_rebuild_locks() -> None:
    """Release both in-process and DB-level rebuild locks."""
    rebuild_state.active = False
    _release_db_advisory_lock("rebuild_vectors")
    _release_db_advisory_lock("rebuild_multimodal")
