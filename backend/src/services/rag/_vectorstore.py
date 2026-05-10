"""Chroma vector store singleton management."""

import concurrent.futures
import functools
import logging
import threading
from typing import Any

from langchain_chroma import Chroma

from ...core.config import settings
from ...core.database._connection import _write_lock as _db_write_lock
from ..embedder import get_embeddings

logger = logging.getLogger(__name__)

# Thread-safe vectorstore singleton via lru_cache (M-CONC-2).
_lock = threading.Lock()
_vectorstore: Chroma | None = None


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
    return Chroma(
        collection_name="meetings",
        embedding_function=embeddings,
        persist_directory=str(settings.VECTOR_DB_DIR),
    )


def reset_vectorstore() -> None:
    """Reset vectorstore singleton to apply updated embedding/runtime settings."""
    global _vectorstore
    _vectorstore = None
    _create_vectorstore.cache_clear()
    logger.info("Vectorstore singleton reset")


def get_vectorstore() -> Chroma:
    """Get or create the singleton Chroma vectorstore (thread-safe).

    If the existing collection's embedding dimension does not match the
    configured ``EMBEDDING_DIMENSION``, the collection is automatically
    dropped and recreated.  All indexed data is lost and must be re-processed
    (meetings will remain in the database with status ``ready`` but their
    vectors are gone).
    """
    global _vectorstore
    with _lock:
        if _vectorstore is None:
            _create_vectorstore.cache_clear()
            _vectorstore = _create_vectorstore()
        return _vectorstore


def _ensure_collection_dimension(
    persist_dir: str,
    name: str,
    embeddings: Any,
    expected_dim: int,
) -> None:
    """Drop collection if its embedding dimension doesn't match *expected_dim*.

    If the collection does not exist, is empty, or dimensions match -- do nothing.
    This is called before creating the LangChain Chroma wrapper so that a
    mismatching collection is cleared first.
    """
    import chromadb

    client = chromadb.PersistentClient(path=persist_dir)
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
        logger.warning(
            "Embedding dimension mismatch: collection '%s' has %d, config expects %d. "
            "Dropping and recreating. Re-process documents to re-index.",
            name,
            stored_dim,
            expected_dim,
        )
        client.delete_collection(name=name)
    elif stored_dim is not None:
        logger.info("Collection '%s' dimension OK (%d)", name, stored_dim)


def _resolve_expected_dimension(embeddings: Any, fallback_dim: int) -> int:
    """Resolve active embedding dimension from provider, with config fallback."""
    for attr in ("embedding_dimension", "dimension", "dimensions"):
        candidate = getattr(embeddings, attr, None)
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    try:
        # Run in a separate thread so the embedder's async-context guard passes.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            probe = pool.submit(embeddings.embed_query, "dimension probe").result(timeout=30)
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
