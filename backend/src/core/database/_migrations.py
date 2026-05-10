"""Schema migration system for the SQLite database.

Each migration is a (version, description, sql) tuple applied sequentially on
startup.  The schema_version table tracks which have already run.

Migration locking:
  A ``schema_lock`` table stores an in-progress flag.  Before running
  migrations the runner acquires an exclusive lock and sets the flag.  If
  another worker is already migrating the current worker retries with a
  timeout.  On completion the flag is cleared.

Idempotent ALTERs:
  Every ``ALTER TABLE … ADD COLUMN`` is preceded by a ``PRAGMA table_info()``
  check.  Columns that already exist are silently skipped.  This removes
reliance on catching *duplicate column name* errors.
"""

import logging
import sqlite3

from ._connection import _get_thread_conn, _write_lock
from ._migration_helpers import (
    ALTER_ADD_COLUMN_RE,
    PRAGMA_FK_OFF_RE,
    column_exists,
    migration_columns_already_present,
    split_sql_statements,
)
from ._migration_lock import (
    acquire_migration_lock,
    ensure_lock_table,
    release_migration_lock,
)

logger = logging.getLogger(__name__)


from ._migration_definitions import _MIGRATIONS, SCHEMA_SQL  # noqa: E402, F401


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version (0 if table doesn't exist)."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] or 0
    except sqlite3.OperationalError:
        return 0


def _apply_migration(conn: sqlite3.Connection, sql: str) -> None:
    """Execute migration SQL with idempotent column checks."""
    statements = split_sql_statements(sql)

    for stmt in statements:
        if not stmt:
            continue

        match = ALTER_ADD_COLUMN_RE.match(stmt.strip())
        if match:
            table, column = match.group(1), match.group(2)
            if column_exists(conn, table, column):
                logger.debug(
                    "Skipping ALTER TABLE ADD COLUMN %s.%s (already present)",
                    table,
                    column,
                )
                continue

        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                logger.debug("Skipping migration statement (column exists): %s", exc)
            else:
                raise


def init_db() -> None:
    """Apply all pending migrations and bring the database schema up to date."""
    conn = _get_thread_conn()
    with _write_lock:
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "  version INTEGER PRIMARY KEY,"
                "  description TEXT NOT NULL,"
                "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
            conn.commit()

            ensure_lock_table(conn)
            acquire_migration_lock(conn)

            try:
                current = _get_schema_version(conn)
                applied = 0
                last_version = current
                for version, description, sql in _MIGRATIONS:
                    if version <= current:
                        continue

                    if migration_columns_already_present(conn, sql):
                        logger.info(
                            "Migration v%d pre-flight: all columns present, recording as applied",
                            version,
                        )
                        conn.execute(
                            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                            (version, description),
                        )
                        conn.commit()
                        applied += 1
                        last_version = version
                        continue

                    needs_fk_restore = bool(PRAGMA_FK_OFF_RE.search(sql))
                    try:
                        _apply_migration(conn, sql)
                    finally:
                        if needs_fk_restore:
                            conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute(
                        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                        (version, description),
                    )
                    conn.commit()
                    applied += 1
                    last_version = version
                    logger.info("Applied migration v%d: %s", version, description)
            finally:
                release_migration_lock(conn)

            if applied:
                logger.info(
                    "Database migrated from v%d to v%d (%d migrations)",
                    current,
                    last_version,
                    applied,
                )
            else:
                logger.debug("Database schema is up to date (v%d)", current)
            conn.execute("PRAGMA foreign_keys=ON")
            fk_result = conn.execute("PRAGMA foreign_keys").fetchone()
            if not fk_result or fk_result[0] != 1:
                raise RuntimeError(
                    "Foreign keys are not enabled after init_db — data integrity at risk"
                )
        except Exception:
            logger.exception("Database migration failed; rolling back")
            try:
                conn.rollback()
            except Exception:
                logger.exception("Rollback failed; original error preserved above")
            raise
