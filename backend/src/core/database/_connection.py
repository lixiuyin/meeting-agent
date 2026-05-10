"""Thread-local connection pool with WAL mode for concurrent reads.

Provides serialized write access via a write lock to avoid 'database is locked'
errors under concurrent async access.
"""

import logging
import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from weakref import WeakValueDictionary

from fastapi import HTTPException

from ..config import settings

logger = logging.getLogger(__name__)

# Maximum wall-clock duration for a single read transaction before a
# warning is emitted.  Long-held read connections block WAL checkpoint
# TRUNCATE and cause .db-wal file bloat (C-C5).
_MAX_READ_TX_SECONDS = 30

# Maximum wall-clock seconds to wait for a write lock before raising.
_WRITE_LOCK_TIMEOUT = 60

# Thread-local storage for connection pooling
_local = threading.local()
# HIGH-16: User-level write lock pool reduces contention compared to a single
# global RLock.  Hashing user_id distributes writes across 64 buckets so
# concurrent writes from different users don't block each other.
_WRITE_LOCK_POOL_SIZE = 256
_write_lock_pool: list[threading.RLock] = [threading.RLock() for _ in range(_WRITE_LOCK_POOL_SIZE)]
# Backward-compatible alias for code that references the original global lock
# (migrations, database __init__.py). Uses pool[0] which is the default bucket.
_write_lock = _write_lock_pool[0]
_pool_lock = threading.Lock()
_wal_init_lock = threading.Lock()
_wal_initialized_paths: set[str] = set()

# Track write-lock acquisition depth per thread so we can detect nested
# writes that extend the lock duration.  RLock allows recursion within a
# thread, so deep nesting is not a deadlock risk — but it IS a throughput
# risk because the lock is held across the inner operations.
_write_depth_per_thread: dict[int, int] = {}


class _TrackedConnection(sqlite3.Connection):
    """sqlite3.Connection subtype that supports weak references."""


_connections: WeakValueDictionary[int, sqlite3.Connection] = WeakValueDictionary()
# CONC-5: Track last activity time per thread for idle-based WAL checkpoint eviction.
_conn_last_active: dict[int, float] = {}


def _get_thread_conn() -> sqlite3.Connection:
    """Get or create a thread-local connection (one per thread)."""
    conn = getattr(_local, "conn", None)
    target_db = str(settings.DB_PATH)
    # Detect closed or stale connections before use.
    if conn is not None:
        try:
            conn.execute("SELECT 1")
        except Exception:
            logger.debug("Thread-local connection is closed; reconnecting")
            conn = None
            _local.conn = None
    # If the connection points to a different database (e.g. benchmark switched
    # to a new temp DB), close it so we reconnect to the correct file.
    if conn is not None:
        current_db = conn.execute("PRAGMA database_list").fetchone()[2]
        if current_db != target_db:
            try:
                conn.close()
            except Exception:
                logger.debug("Failed to close stale connection during DB switch", exc_info=True)
            conn = None
            _local.conn = None
    if conn is None:
        conn = sqlite3.connect(
            target_db,
            check_same_thread=False,
            factory=_TrackedConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        # WAL mode is a file-level setting; initialize once per DB path to avoid
        # concurrent PRAGMA journal_mode races on first multi-threaded connect.
        if target_db not in _wal_initialized_paths:
            with _wal_init_lock:
                if target_db not in _wal_initialized_paths:
                    conn.execute("PRAGMA journal_mode=WAL")
                    _wal_initialized_paths.add(target_db)
        _local.conn = conn
        with _pool_lock:
            _connections[threading.get_ident()] = conn
    return conn


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Yield a thread-local connection for reads (no lock needed in WAL mode).

    Use get_write_connection() for mutations.

    When a read transaction exceeds ``_MAX_READ_TX_SECONDS``, the connection
    is closed and removed from the thread-local pool to unblock WAL checkpoint
    truncation and prevent .db-wal bloat (HIGH-12).
    """
    conn = _get_thread_conn()
    tx_start = time.monotonic()
    _conn_last_active[threading.get_ident()] = tx_start
    try:
        yield conn
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        logger.error("Read transaction failed; rolling back", exc_info=True)
        conn.rollback()
        raise
    finally:
        elapsed = time.monotonic() - tx_start
        try:
            from ..metrics import DB_READ_TX_AGE_SECONDS

            DB_READ_TX_AGE_SECONDS.observe(elapsed)
        except Exception:
            pass  # metrics are optional; never fail a read for a gauge
        if elapsed > _MAX_READ_TX_SECONDS:
            logger.warning(
                "Read transaction held for %.0fs (> %ds max); closing "
                "connection to unblock WAL checkpoint truncation and "
                "prevent .db-wal bloat.  Stack trace:",
                elapsed,
                _MAX_READ_TX_SECONDS,
                stack_info=True,
            )
            try:
                conn.close()
                logger.debug("Closed long-held read connection (%.0fs)", elapsed)
            except Exception:
                logger.debug("Failed to close long-held read connection", exc_info=True)
            _local.conn = None


@contextmanager
def get_write_connection(
    user_id: str | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Yield a thread-local connection with serialized write access.

    HIGH-16: Uses a bucketed lock pool (64 locks, hashed by ``user_id``).
    When ``user_id`` is omitted, falls back to the global lock for backward
    compatibility with internal operations (schema migrations, startup).
    """
    tid = threading.get_ident()
    conn = _get_thread_conn()
    if user_id is not None:
        # CONC-4: Deterministic hashing avoids PYTHONHASHSEED variance.
        import hashlib as _hashlib

        bucket = (
            int.from_bytes(_hashlib.blake2b(user_id.encode(), digest_size=4).digest(), "big")
            % _WRITE_LOCK_POOL_SIZE
        )
        lock = _write_lock_pool[bucket]
    else:
        # Legacy global lock for operations without user context.
        lock = _write_lock_pool[0]
    acquired = lock.acquire(timeout=_WRITE_LOCK_TIMEOUT)
    if not acquired:
        raise RuntimeError(
            f"Write lock acquisition timed out after {_WRITE_LOCK_TIMEOUT}s "
            f"(bucket user_id={user_id!r}). Check for long-held write transactions."
        )
    try:
        depth = _write_depth_per_thread.get(tid, 0) + 1
        _write_depth_per_thread[tid] = depth
        if depth >= 2:
            logger.warning(
                "Nested write connection (depth=%d); consider refactoring "
                "to avoid extending the write-lock duration.",
                depth,
            )
        if depth > 3:
            raise RuntimeError(
                f"Nested write lock too deep (depth={depth}); "
                "refactor to avoid extending write-lock duration."
            )
        try:
            yield conn
            conn.commit()
        except HTTPException:
            try:
                conn.rollback()
            except Exception:
                logger.debug("Rollback failed (connection may be closed)", exc_info=True)
            raise
        except Exception as exc:
            logger.error("Write transaction failed; rolling back", exc_info=True)
            try:
                conn.rollback()
            except Exception:
                logger.debug("Rollback failed (connection may be closed)", exc_info=True)
            if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
                from ..metrics import SQLITE_BUSY_TIMEOUTS_TOTAL

                SQLITE_BUSY_TIMEOUTS_TOTAL.inc()
            raise
        finally:
            new_depth = _write_depth_per_thread.get(tid, 1) - 1
            if new_depth <= 0:
                _write_depth_per_thread.pop(tid, None)
            else:
                _write_depth_per_thread[tid] = new_depth
    finally:
        lock.release()


def close_all_connections() -> None:
    """Close all currently known pooled connections."""
    current_conn = getattr(_local, "conn", None)
    with _pool_lock:
        conns = list(_connections.values())
        _connections.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception:
            logger.warning("Failed to close sqlite connection", exc_info=True)
    if current_conn is not None:
        _local.conn = None
