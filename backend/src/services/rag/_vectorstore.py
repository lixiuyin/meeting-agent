"""Chroma vector store singleton management."""

import concurrent.futures
import functools
import logging
import threading
from typing import Any

from langchain_chroma import Chroma

from ...core._config_snapshot import submit_with_context
from ...core.config import settings
from ...core.database._connection import _write_lock as _db_write_lock
from ..embedder import get_embeddings

logger = logging.getLogger(__name__)

# Thread-safe vectorstore singleton via lru_cache (M-CONC-2).
_lock = threading.Lock()
_vectorstore: Chroma | None = None
_vectorstore_key: tuple[Any, ...] | None = None
_DIMENSION_PROBE_TIMEOUT = 30.0
_dimension_probe_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="embedding-dimension"
)
_dimension_probe_slot = threading.Lock()


class _LiveCollection:
    """Late-bind cached LangChain handles under the same lock as publication.

    Embeddings are computed by LangChain before the local collection call, so
    this does not hold SQLite's write lock during provider requests.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        with _db_write_lock:
            attribute = getattr(self._client.get_collection("meetings"), name)
        if not callable(attribute):
            return attribute

        def invoke(*args, **kwargs):
            with _db_write_lock:
                return getattr(self._client.get_collection("meetings"), name)(*args, **kwargs)

        return invoke


@functools.lru_cache(maxsize=1)
def _create_vectorstore() -> Chroma:
    """Factory for the singleton Chroma vectorstore.  Cache is cleared on reset."""
    embeddings = get_embeddings()
    expected_dim = _resolve_expected_dimension(embeddings, settings.EMBEDDING_DIMENSION)
    _ensure_collection_dimension(
        str(settings.VECTOR_DB_DIR),
        "meetings",
        embeddings,
        expected_dim,
    )
    store = Chroma(
        collection_name="meetings",
        embedding_function=embeddings,
        persist_directory=str(settings.VECTOR_DB_DIR),
    )
    store._chroma_collection = _LiveCollection(store._client)  # type: ignore[assignment]
    return store


def reset_vectorstore() -> None:
    """Reset vectorstore singleton to apply updated embedding/runtime settings."""
    global _vectorstore, _vectorstore_key
    with _lock:
        _vectorstore = None
        _vectorstore_key = None
        _create_vectorstore.cache_clear()
    logger.info("Vectorstore singleton reset")


def get_vectorstore() -> Chroma:
    """Get or create the singleton Chroma vectorstore (thread-safe).

    A dimension mismatch fails closed and requires an explicit rebuild; startup
    must never destroy the only search index as a side effect of config drift.
    """
    global _vectorstore, _vectorstore_key
    embeddings = get_embeddings()
    config_key = (
        str(settings.VECTOR_DB_DIR),
        settings.EMBEDDING_BINDING,
        settings.EMBEDDING_MODEL,
        settings.EMBEDDING_DIMENSION,
        settings.EMBEDDING_BASE_URL,
        settings.EMBEDDING_HOST,
        id(embeddings),
    )
    with _lock:
        if _vectorstore is None or _vectorstore_key != config_key:
            _create_vectorstore.cache_clear()
            _vectorstore = _create_vectorstore()
            _vectorstore_key = config_key
        return _vectorstore


def _ensure_collection_dimension(
    persist_dir: str,
    name: str,
    embeddings: Any,
    expected_dim: int,
) -> None:
    """Reject a collection whose embedding dimension doesn't match.

    If the collection does not exist, is empty, or dimensions match -- do nothing.
    An operator can then run the explicit shadow/rebuild workflow while the
    existing collection remains recoverable.
    """
    import chromadb

    from ...core.chroma_security import validate_chroma_runtime

    client: Any = chromadb.PersistentClient(
        path=str(validate_chroma_runtime(persist_dir=persist_dir))
    )
    try:
        _check_collection_dimension(client, name, expected_dim)
    finally:
        # A probe owns a client reference even when the collection is absent.
        # Repeated rebuilds/evaluation cases must release it on every branch.
        client.close()


def _check_collection_dimension(client: Any, name: str, expected_dim: int) -> None:
    try:
        col = client.get_collection(name=name)
    except Exception:
        return

    if col.count() == 0:
        return

    # Try to read dimension from collection metadata first
    stored_dim = (col.metadata or {}).get("embedding_dimension")

    # Fall back to sampling a single vector
    if stored_dim is None:
        try:
            peek = col.get(include=["embeddings"], limit=1)
            embs = peek.get("embeddings")
            if embs is not None and len(embs) > 0:
                first = embs[0]
                if first is not None and hasattr(first, "__len__"):
                    stored_dim = len(first)
        except Exception:
            logger.debug(
                "Failed to peek at collection '%s' for dimension check",
                name,
                exc_info=True,
            )

    if stored_dim is not None and stored_dim != expected_dim:
        # MEDIUM-1: Emit Prometheus metric for dimension-change events.
        try:
            from ...core.metrics import STARTUP_BEST_EFFORT_FAILURES_TOTAL

            STARTUP_BEST_EFFORT_FAILURES_TOTAL.labels(task="embedding_dimension_change").inc()
        except Exception:
            pass  # metrics are optional
        message = (
            "Embedding dimension mismatch: collection '%s' has %d, config expects %d. "
            "Refusing to delete existing vectors; run an explicit index rebuild."
        )
        logger.error(message, name, stored_dim, expected_dim)
        raise RuntimeError(message % (name, stored_dim, expected_dim))
    elif stored_dim is not None:
        logger.info("Collection '%s' dimension OK (%d)", name, stored_dim)


def _resolve_expected_dimension(embeddings: Any, fallback_dim: int) -> int:
    """Resolve active embedding dimension from provider, with config fallback."""
    for attr in ("embedding_dimension", "dimension", "dimensions"):
        candidate = getattr(embeddings, attr, None)
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    if not _dimension_probe_slot.acquire(blocking=False):
        return fallback_dim
    try:
        future = submit_with_context(
            _dimension_probe_executor, embeddings.embed_query, "dimension probe"
        )
    except BaseException:
        _dimension_probe_slot.release()
        raise
    # A timed-out thread cannot be killed. Keep its slot until completion so
    # repeated requests cannot grow an unbounded backlog of provider calls.
    future.add_done_callback(lambda _future: _dimension_probe_slot.release())
    try:
        probe = future.result(timeout=_DIMENSION_PROBE_TIMEOUT)
        if isinstance(probe, list) and probe:
            return len(probe)
    except Exception:
        logger.debug("Failed to infer embedding dimension from provider", exc_info=True)
    return fallback_dim


def persist_vectorstore() -> None:
    """No-op: langchain-chroma 0.1.4+ auto-persists via PersistentClient."""
    pass


def vectorstore_write_lock() -> threading.RLock:
    """Global write lock for Chroma mutations (upsert/delete).

    Shares the same lock as SQLite writes to prevent orphaned vectors/rows
    when one write succeeds and the other fails.

    NOTE: This ``threading.Lock`` only serialises writes *within a single
    process*.  Chroma PersistentClient holds its own SQLite lock on the
    ``chroma.sqlite3`` file, so multi-process deployments would need a
    process-level lock (e.g. ``filelock``) instead.  The current
    single-instance deployment constraint (see ADR-006) makes the
    in-process lock sufficient.
    """
    return _db_write_lock
