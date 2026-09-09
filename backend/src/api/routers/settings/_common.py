"""Settings router shared state and router instance."""

import asyncio
import logging
import threading
import uuid
from typing import Literal

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
    hybrid_search_enabled: bool | None = None,
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
    effective_hybrid_enabled = (
        hybrid_search_enabled
        if hybrid_search_enabled is not None
        else settings.HYBRID_SEARCH_ENABLED
    )

    errors: list[str] = []

    if effective_reranker_top_n < effective_top_k:
        errors.append(
            f"RERANKER_TOP_N ({effective_reranker_top_n}) must be >= TOP_K ({effective_top_k})"
        )

    if not (0.0 <= effective_alpha <= 1.0):
        errors.append(f"HYBRID_ALPHA ({effective_alpha}) must be between 0 and 1")

    if effective_provider == "hybrid" and not effective_hybrid_enabled:
        errors.append("retriever_provider 'hybrid' requires HYBRID_SEARCH_ENABLED=true")

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))


def _normalize_retriever_provider(value: str) -> str:
    mode = value.strip().lower()
    if mode == "native":
        return "vector"
    if mode in {"vector", "hybrid", "multimodal", "hybrid_multimodal"}:
        return mode
    return "vector"


_active_rebuild_tasks: set[asyncio.Task] = set()


class _RebuildState:
    """Single global flag for all index rebuild operations.

    Vector and multimodal rebuilds both write to Chroma; allowing them to
    run concurrently risks interleaved delete+upsert sequences that corrupt
    index state (C-C4). A single lock serializes all rebuilds.
    """

    active: bool = False
    vector_result: Literal["idle", "running", "completed", "failed", "cancelled"] = "idle"

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


_GLOBAL_REBUILD_LOCK = "rebuild_global"
_advisory_owners: dict[str, str] = {}


def _try_acquire_db_advisory_lock(lock_name: str = _GLOBAL_REBUILD_LOCK) -> bool:
    """Atomically acquire the cross-process rebuild lock.

    ``get_write_connection`` starts a serialized write transaction, so the
    read, stale-lock decision and claim cannot race another process.
    """
    import time

    from ....core.database import get_write_connection

    owner = uuid.uuid4().hex
    try:
        with get_write_connection() as conn:
            row = conn.execute("SELECT value FROM kv_state WHERE key=?", (lock_name,)).fetchone()
            if row and row["value"] == "locked":
                locked_at = conn.execute(
                    "SELECT value FROM kv_state WHERE key=?",
                    (f"{lock_name}_at",),
                ).fetchone()
                if not locked_at:
                    return False
                try:
                    if time.time() - float(locked_at["value"]) <= 1800:
                        return False
                except (ValueError, TypeError):
                    return False
                conn.execute(
                    "DELETE FROM kv_state WHERE key IN (?, ?)",
                    (lock_name, f"{lock_name}_at"),
                )
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value) VALUES (?, 'locked')",
                (lock_name,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value) VALUES (?, ?)",
                (f"{lock_name}_at", str(time.time())),
            )
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value) VALUES (?, ?)",
                (f"{lock_name}_owner", owner),
            )
    except Exception:
        logger.warning("DB advisory lock acquisition failed for %s", lock_name, exc_info=True)
        return False
    _advisory_owners[lock_name] = owner
    return True


def _release_db_advisory_lock(lock_name: str) -> None:
    """Release the DB-based advisory lock."""
    from ....core.database import get_write_connection

    owner = _advisory_owners.pop(lock_name, None)
    if owner is None:
        return
    try:
        with get_write_connection() as conn:
            current = conn.execute(
                "SELECT value FROM kv_state WHERE key=?", (f"{lock_name}_owner",)
            ).fetchone()
            if current and current["value"] == owner:
                conn.execute(
                    "DELETE FROM kv_state WHERE key IN (?, ?, ?)",
                    (lock_name, f"{lock_name}_at", f"{lock_name}_owner"),
                )
    except Exception:
        logger.warning("DB advisory lock release failed for %s", lock_name, exc_info=True)


def renew_rebuild_advisory_lock(lock_name: str = _GLOBAL_REBUILD_LOCK) -> bool:
    """Renew only the lease still owned by this process."""
    import time

    from ....core.database import get_write_connection

    owner = _advisory_owners.get(lock_name)
    if owner is None:
        return False
    try:
        with get_write_connection() as conn:
            current = conn.execute(
                "SELECT value FROM kv_state WHERE key=?", (f"{lock_name}_owner",)
            ).fetchone()
            locked = conn.execute("SELECT value FROM kv_state WHERE key=?", (lock_name,)).fetchone()
            if (
                not current
                or current["value"] != owner
                or not locked
                or locked["value"] != "locked"
            ):
                return False
            conn.execute(
                "UPDATE kv_state SET value=?,updated_at=CURRENT_TIMESTAMP WHERE key=?",
                (str(time.time()), f"{lock_name}_at"),
            )
        return True
    except Exception:
        logger.warning("DB advisory lock renewal failed for %s", lock_name, exc_info=True)
        return False


def try_acquire_vectors_rebuild() -> bool:
    """Atomically claim the rebuild slot. Returns False if any rebuild is in progress."""
    with _settings_lock:
        if rebuild_state.active:
            return False
        if not _try_acquire_db_advisory_lock():
            return False
        rebuild_state.active = True
        return True


def try_acquire_multimodal_rebuild() -> bool:
    """Atomically claim the rebuild slot. Returns False if any rebuild is in progress."""
    with _settings_lock:
        if rebuild_state.active:
            return False
        if not _try_acquire_db_advisory_lock():
            return False
        rebuild_state.active = True
        return True


def release_all_rebuild_locks() -> None:
    """Release both in-process and DB-level rebuild locks."""
    rebuild_state.active = False
    _release_db_advisory_lock(_GLOBAL_REBUILD_LOCK)
    # Remove legacy per-rebuild keys left by earlier releases.
    _release_db_advisory_lock("rebuild_vectors")
    _release_db_advisory_lock("rebuild_multimodal")
