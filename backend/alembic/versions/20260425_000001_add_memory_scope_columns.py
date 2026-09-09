"""Add scope (meeting_ids / file_ids) columns to user_memories and memory_entities.

Revision ID: 20260425_000001
Revises: 20260414_000001
Create Date: 2026-04-25 01:20:00

Matches legacy migration v28. Required so new-format reads (``list_memories``
etc.) don't fail with ``sqlite3.OperationalError: no such column: meeting_ids``
on databases whose Alembic baseline was applied before these columns existed.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy.exc import OperationalError

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260425_000001"
down_revision = "20260414_000001"
branch_labels = None
depends_on = None


_ALTER_STATEMENTS = [
    "ALTER TABLE user_memories ADD COLUMN meeting_ids TEXT",
    "ALTER TABLE user_memories ADD COLUMN file_ids TEXT",
    "ALTER TABLE memory_entities ADD COLUMN meeting_ids TEXT",
    "ALTER TABLE memory_entities ADD COLUMN file_ids TEXT",
]


def _execute_tolerant(sql: str) -> None:
    """Execute an ALTER, swallowing "duplicate column" errors so this
    migration is idempotent against DBs where the legacy _MIGRATIONS path
    already applied the same ALTER via init_db()."""
    try:
        op.execute(sql)
    except (OperationalError, sqlite3.OperationalError) as exc:
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return
        raise


def upgrade() -> None:
    for stmt in _ALTER_STATEMENTS:
        _execute_tolerant(stmt)


def downgrade() -> None:
    # Python 3.12 ships SQLite with DROP COLUMN support. Using it preserves
    # primary keys, foreign keys, indexes and triggers; CREATE TABLE AS would
    # silently discard all of them.
    for table in ("user_memories", "memory_entities"):
        existing = {
            row[1]
            for row in op.get_bind().exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        }
        for column in ("meeting_ids", "file_ids"):
            if column in existing:
                op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
