"""Migration locking primitives.

Prevents concurrent workers from running schema migrations simultaneously
via a ``schema_lock`` table with retry and stale-lock detection.
"""

import logging
import sqlite3
import time

logger = logging.getLogger(__name__)

_MIGRATION_LOCK_TIMEOUT_SECONDS = 30
_MIGRATION_LOCK_RETRY_INTERVAL_SECONDS = 0.5


def ensure_lock_table(conn: sqlite3.Connection) -> None:
    """Create the schema_lock table if it does not exist."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_lock ("
        "  id INTEGER PRIMARY KEY CHECK (id = 1),"
        "  is_locked INTEGER NOT NULL DEFAULT 0,"
        "  locked_at TIMESTAMP,"
        "  locked_by TEXT"
        ")"
    )
    conn.execute("INSERT OR IGNORE INTO schema_lock (id, is_locked) VALUES (1, 0)")
    conn.commit()


def acquire_migration_lock(conn: sqlite3.Connection) -> None:
    """Acquire the migration lock with retry and timeout."""
    import os
    import uuid

    lock_id = f"pid-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    deadline = time.monotonic() + _MIGRATION_LOCK_TIMEOUT_SECONDS

    while True:
        conn.execute(
            "UPDATE schema_lock "
            "SET is_locked = 1, locked_at = CURRENT_TIMESTAMP, locked_by = ? "
            "WHERE id = 1 AND is_locked = 0",
            (lock_id,),
        )
        conn.commit()

        row = conn.execute("SELECT is_locked, locked_by FROM schema_lock WHERE id = 1").fetchone()

        if row is not None and row["is_locked"] == 1 and row["locked_by"] == lock_id:
            logger.debug("Acquired migration lock (%s)", lock_id)
            return

        if row is not None and row["is_locked"] == 1:
            locked_by = row["locked_by"] or ""
            # Fast-check: if the lock holder's PID is dead, clear immediately.
            _pid_dead = False
            if locked_by.startswith("pid-"):
                try:
                    import os as _os

                    _pid = int(locked_by.split("-")[1])
                    _os.kill(_pid, 0)  # signal 0 = check existence
                except (OSError, ValueError, IndexError):
                    _pid_dead = True

            if _pid_dead:
                logger.warning("Clearing migration lock from dead process %s", locked_by)
                _clear_lock(conn)
                continue

            locked_at = row["locked_at"]
            if locked_at is not None:
                lock_age_seconds = max(15, _MIGRATION_LOCK_TIMEOUT_SECONDS)
                try:
                    import datetime

                    locked_dt = datetime.datetime.fromisoformat(str(locked_at))
                    elapsed = (datetime.datetime.now(datetime.UTC) - locked_dt).total_seconds()
                    if elapsed > lock_age_seconds:
                        logger.warning(
                            "Clearing stale migration lock held by %s (held for %.0fs)",
                            row["locked_by"],
                            elapsed,
                        )
                        _clear_lock(conn)
                        continue
                except (ValueError, TypeError):
                    pass

        if time.monotonic() >= deadline:
            holder = row["locked_by"] if row is not None else "unknown"
            raise RuntimeError(
                f"Could not acquire migration lock within "
                f"{_MIGRATION_LOCK_TIMEOUT_SECONDS}s "
                f"(held by: {holder})"
            )

        remaining = deadline - time.monotonic()
        sleep_time = min(_MIGRATION_LOCK_RETRY_INTERVAL_SECONDS, max(0, remaining))
        time.sleep(sleep_time)


def _clear_lock(conn: sqlite3.Connection) -> None:
    """Clear the migration lock unconditionally."""
    conn.execute(
        "UPDATE schema_lock SET is_locked = 0, locked_at = NULL, locked_by = NULL WHERE id = 1"
    )
    conn.commit()


def release_migration_lock(conn: sqlite3.Connection) -> None:
    """Release the migration lock."""
    _clear_lock(conn)
    logger.debug("Released migration lock")
