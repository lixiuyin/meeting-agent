"""BM25 index maintenance: load checks, rebuild, and drift detection."""

import json
import logging
import threading
from functools import wraps
from typing import Any

from ...core.config import settings
from ._vectorstore import get_vectorstore, vectorstore_write_lock

logger = logging.getLogger(__name__)

# H-CONC-3: Global flag indicating BM25 rebuild in progress.
# Retrievers check this to fall back to pure-vector mode during rebuild.
_bm25_rebuilding = False
_bm25_rebuilding_lock = threading.Lock()


def is_bm25_rebuilding() -> bool:
    """Return True if BM25 rebuild is in progress."""
    return _bm25_rebuilding


def load_bm25_from_database() -> bool:
    """Check if FTS5 index has data. No in-memory rebuild needed with FTS5."""
    from ...core.database import get_connection

    try:
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM bm25_index").fetchone()
            count = row["cnt"] if row else 0
        if count > 0:
            logger.info("FTS5 index ready (%d chunks in database)", count)
            return True
        logger.info("No FTS5 index data found")
        return False
    except Exception as e:
        logger.warning("Failed to check FTS5 index: %s", e)
        return False


def _serialized_maintenance(function):
    @wraps(function)
    def wrapped(force=False, timeout=None, *, strict=False, source=None):
        if not force and load_bm25_from_database():
            return True
        try:
            # Resolve provider/dimension outside the local publication lock.
            source = source if source is not None else get_vectorstore()
            with vectorstore_write_lock():
                return function(force, timeout, strict=strict, source=source)
        except Exception:
            if strict:
                raise
            logger.warning("BM25 maintenance failed", exc_info=True)
            return False

    return wrapped


@_serialized_maintenance
def rebuild_bm25_from_chroma(
    force: bool = False,
    timeout: float | None = None,
    *,
    strict: bool = False,
    source: Any = None,
) -> bool:
    """Rebuild FTS5 index from existing Chroma data.

    By default, only rebuilds when FTS5 is empty.  Pass ``force=True``
    to always rebuild (used when drift is detected between the two
    indexes).

    Args:
        force: Rebuild even if FTS5 is non-empty.
        timeout: Maximum wall-clock seconds for the rebuild.  ``None``
            means no limit.  If exceeded, the rebuild is aborted but
            the live index is left intact (staging table is cleaned up).
        strict: Re-raise failures so an orchestrated vector swap can roll
            back instead of reporting a false-successful cross-store rebuild.

    H-12: Uses a staging table so the live index remains fully intact
    during the rebuild.  The swap (delete old + insert new) happens in a
    single write transaction, ensuring readers never see an empty or
    partial index.

    Emits progress logs at 10 % intervals so operators can monitor
    long-running rebuilds.
    """
    if not force and load_bm25_from_database():
        return True

    global _bm25_rebuilding
    with _bm25_rebuilding_lock:
        _bm25_rebuilding = True

    import time

    start_time = time.monotonic()
    _timed_out = False

    def _check_timeout(label: str) -> None:
        nonlocal _timed_out
        if timeout is None or _timed_out:
            return
        elapsed = time.monotonic() - start_time
        if elapsed > timeout:
            _timed_out = True
            raise TimeoutError(f"BM25 rebuild exceeded {timeout:.0f}s at {label}")

    try:
        vectorstore = source if source is not None else get_vectorstore()
        from ...core.database import get_write_connection

        indexed = 0

        # H-12 Phase 1: Build new data in staging table (no interference with live index).
        with get_write_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS bm25_index_staging")
            conn.execute(
                "CREATE TABLE bm25_index_staging ("
                "chunk_id TEXT NOT NULL UNIQUE, "
                "meeting_id INTEGER NOT NULL, "
                "content TEXT NOT NULL, "
                "tokenized TEXT NOT NULL, "
                "metadata TEXT)"
            )

        offset = 0
        batch_size = 1000
        while True:
            _check_timeout("batch read")
            batch = vectorstore.get(
                include=["documents", "metadatas"], limit=batch_size, offset=offset
            )
            ids = batch.get("ids") or []
            if not ids:
                break
            staging_rows = []
            for chunk_id, content, meta in zip(
                ids, batch.get("documents") or [], batch.get("metadatas") or [], strict=True
            ):
                _check_timeout("staging collect loop")
                if not chunk_id or not isinstance(meta, dict):
                    raise RuntimeError("BM25 rebuild encountered incomplete chunk metadata/ID")
                if meta.get("chunk_type") == "parent":
                    continue
                staging_rows.append(
                    (chunk_id, meta.get("meeting_id", 0), content, "[]", json.dumps(meta))
                )
                indexed += 1
            with get_write_connection() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO bm25_index_staging "
                    "(chunk_id, meeting_id, content, tokenized, metadata) "
                    "VALUES (?, ?, ?, ?, ?)",
                    staging_rows,
                )
            offset += len(ids)
            logger.info("BM25 rebuild staged %d child chunks", indexed)
            if len(ids) < batch_size:
                break

        # H-12 Phase 2: Atomic swap — delete old + insert new in one transaction.
        # The FTS5 triggers on bm25_index handle the FTS side automatically.
        _check_timeout("atomic swap")
        with get_write_connection() as conn:
            conn.execute("DELETE FROM bm25_index")
            conn.execute(
                "INSERT INTO bm25_index (chunk_id, meeting_id, content, tokenized, metadata) "
                "SELECT chunk_id, meeting_id, content, tokenized, metadata "
                "FROM bm25_index_staging"
            )
            # Reset incremental backfill cursor so it doesn't reference
            # old (now-deleted) row IDs.  New rows already carry chunk_id
            # in their metadata, so the backfill is a no-op for them.
            conn.execute("DELETE FROM bm25_stats WHERE key='last_indexed_id'")
            conn.execute("DROP TABLE IF EXISTS bm25_index_staging")

        logger.info("FTS5 index rebuilt from Chroma: %d chunks", indexed)
        return True
    except Exception as e:
        logger.warning("Failed to rebuild FTS5 from Chroma: %s", e, exc_info=True)
        # Clean up staging table on failure
        try:
            from ...core.database import get_write_connection

            with get_write_connection() as conn:
                conn.execute("DROP TABLE IF EXISTS bm25_index_staging")
        except Exception:
            logger.debug("Failed to drop bm25_index_staging during cleanup", exc_info=True)
        if strict:
            raise
        return False
    finally:
        with _bm25_rebuilding_lock:
            _bm25_rebuilding = False


def check_and_rebuild_bm25_if_drifted() -> None:
    """Detect drift between FTS5 and Chroma, trigger rebuild if significant.

    Drift is defined as a row-count difference exceeding a configurable
    threshold (default 10%).  Called during startup after both stores
    are initialized.
    """
    # Count Chroma child chunks (non-parent)
    vectorstore = get_vectorstore()
    try:
        all_data = vectorstore.get(include=["metadatas"])
    except Exception:
        logger.debug("Cannot check BM25 drift — Chroma unavailable")
        return

    chroma_count = sum(
        1 for m in (all_data.get("metadatas") or []) if m.get("chunk_type") != "parent"
    )

    # Count FTS5 rows
    from ...core.database import get_connection

    try:
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM bm25_index").fetchone()
            fts5_count = row["cnt"] if row else 0
    except Exception:
        logger.debug("Cannot check BM25 drift — FTS5 table missing")
        return

    if fts5_count == 0 and chroma_count > 0:
        logger.info("FTS5 empty but Chroma has %d chunks — rebuilding", chroma_count)
        rebuild_bm25_from_chroma(force=True)
        return

    if chroma_count == 0:
        return

    drift_pct = abs(chroma_count - fts5_count) / chroma_count
    drift_threshold = getattr(settings, "BM25_DRIFT_THRESHOLD", 0.10)
    if drift_pct > drift_threshold:
        logger.warning(
            "FTS5/Chroma drift detected: FTS5=%d, Chroma=%d (%.1f%%) — rebuilding",
            fts5_count,
            chroma_count,
            drift_pct * 100,
        )
        rebuild_bm25_from_chroma(force=True)
    else:
        logger.debug(
            "FTS5/Chroma in sync: FTS5=%d, Chroma=%d (%.1f%% drift)",
            fts5_count,
            chroma_count,
            drift_pct * 100,
        )
