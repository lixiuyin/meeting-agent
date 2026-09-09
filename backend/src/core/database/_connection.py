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
from pathlib import Path
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
# SQLite WAL still permits only one writer per database file.  Multiple
# user-bucket locks merely allow contenders to collide inside SQLite and cause
# busy errors, so all writes share one process-level queue/lock.
_WRITE_LOCK_POOL_SIZE = 1
_write_lock_pool: list[threading.RLock] = [threading.RLock() for _ in range(_WRITE_LOCK_POOL_SIZE)]
_write_lock = _write_lock_pool[0]
_pool_lock = threading.RLock()
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
# Number of active connection contexts per thread. WAL recovery must never
# close a connection while another thread is using it.
_conn_active: dict[int, int] = {}


def _mark_conn_active(tid: int) -> None:
    with _pool_lock:
        _conn_active[tid] = _conn_active.get(tid, 0) + 1


def _mark_conn_idle(tid: int) -> None:
    with _pool_lock:
        depth = _conn_active.get(tid, 1) - 1
        if depth <= 0:
            _conn_active.pop(tid, None)
            _conn_last_active[tid] = time.monotonic()
        else:
            _conn_active[tid] = depth


def _get_thread_conn() -> sqlite3.Connection:
    """Get or create a thread-local connection (one per thread)."""
    conn = getattr(_local, "conn", None)
    target_db = str(Path(settings.DB_PATH).resolve())
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
        current_db = str(Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve())
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
        # SQLite creates the main/WAL/shared-memory files using the caller's
        # umask. Normalize them for deployments launched outside our scripts.
        for database_file in (
            Path(target_db),
            Path(f"{target_db}-wal"),
            Path(f"{target_db}-shm"),
        ):
            if database_file.exists():
                try:
                    database_file.chmod(0o600)
                except OSError:
                    logger.warning(
                        "Could not restrict database file permissions: %s", database_file
                    )
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
    tid = threading.get_ident()
    # Resolve and mark the connection as active under the same lock used by
    # WAL recovery. This closes the small window where another thread could
    # evict a connection between lookup and activity registration.
    with _pool_lock:
        conn = _get_thread_conn()
        _mark_conn_active(tid)
    tx_start = time.monotonic()
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
        _mark_conn_idle(tid)
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
            with _pool_lock:
                if _conn_active.get(tid, 0) == 0:
                    try:
                        conn.close()
                        logger.debug("Closed long-held read connection (%.0fs)", elapsed)
                    except Exception:
                        logger.debug("Failed to close long-held read connection", exc_info=True)
                    _connections.pop(tid, None)
                    _conn_last_active.pop(tid, None)
                    _local.conn = None


@contextmanager
def get_write_connection(
    user_id: str | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Yield a thread-local connection with serialized write access.

    SQLite has a single writer per database file, so ``user_id`` is accepted
    only for API compatibility and all callers enter the same writer queue.
    """
    _ = user_id
    tid = threading.get_ident()
    with _pool_lock:
        conn = _get_thread_conn()
        _mark_conn_active(tid)
    lock = _write_lock
    acquired = lock.acquire(timeout=_WRITE_LOCK_TIMEOUT)
    if not acquired:
        _mark_conn_idle(tid)
        raise RuntimeError(
            f"Write lock acquisition timed out after {_WRITE_LOCK_TIMEOUT}s "
            f"(bucket user_id={user_id!r}). Check for long-held write transactions."
        )
    try:
        previous_depth = _write_depth_per_thread.get(tid, 0)
        depth = previous_depth + 1
        _write_depth_per_thread[tid] = depth
        if depth >= 2:
            logger.warning(
                "Nested write connection (depth=%d); consider refactoring "
                "to avoid extending the write-lock duration.",
                depth,
            )
        savepoint = f"meeting_agent_nested_{depth}" if previous_depth else None
        try:
            if savepoint is not None:
                conn.execute(f"SAVEPOINT {savepoint}")
            elif not conn.in_transaction:
                # Acquire SQLite's writer reservation before any read/claim.
                # An outer context without DML must still own a transaction:
                # otherwise RELEASE of a nested SAVEPOINT commits early.
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if savepoint is not None:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                from ..chat_run_context import fence_run_commit
                from ..idempotency_context import fence_commit
                from ..job_fence import assert_active_job_fence
                from ..source_revision_fence import assert_active_source_revision_fence

                assert_active_job_fence(conn)
                assert_active_source_revision_fence(conn)
                fence_commit(conn)
                fence_run_commit(conn)
                conn.commit()
        except HTTPException:
            try:
                if savepoint is not None:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    conn.rollback()
            except Exception:
                logger.debug("Rollback failed (connection may be closed)", exc_info=True)
            raise
        except BaseException as exc:
            logger.error("Write transaction failed; rolling back", exc_info=True)
            try:
                if savepoint is not None:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
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
        _mark_conn_idle(tid)
        lock.release()


def close_all_connections() -> None:
    """Close all currently known pooled connections."""
    current_conn = getattr(_local, "conn", None)
    with _pool_lock:
        conns = list(_connections.values())
        _connections.clear()
        _conn_active.clear()
        _conn_last_active.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception:
            logger.warning("Failed to close sqlite connection", exc_info=True)
    if current_conn is not None:
        _local.conn = None
