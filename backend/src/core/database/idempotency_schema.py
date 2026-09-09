"""Compatibility bootstrap for durable idempotency lifecycle metadata."""

import sqlite3

IDEMPOTENCY_LIFECYCLE_SCHEMA_SQL = """
ALTER TABLE idempotency_keys
    ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'legacy_unknown';
CREATE INDEX IF NOT EXISTS idx_idempotency_lifecycle_expiry
    ON idempotency_keys(lifecycle_state, expires_at);
"""


def ensure_idempotency_lifecycle_schema(conn: sqlite3.Connection) -> None:
    """Keep isolated tests and the emergency legacy bootstrap schema current."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(idempotency_keys)")}
    if "lifecycle_state" not in columns:
        conn.execute(
            "ALTER TABLE idempotency_keys "
            "ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'legacy_unknown'"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_idempotency_lifecycle_expiry "
        "ON idempotency_keys(lifecycle_state, expires_at)"
    )
