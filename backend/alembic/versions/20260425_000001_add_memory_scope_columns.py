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
    # Drop scope columns via recreate pattern for each table
    for table in ("user_memories", "memory_entities"):
        _recreate_without_columns(table, {"meeting_ids", "file_ids"})


def _get_columns_to_keep(bind, table: str, drop_cols: set[str]) -> list[str]:
    """Return column names from *table* excluding *drop_cols*."""
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return [row[1] for row in rows if row[1] not in drop_cols]


def _recreate_without_columns(table: str, drop_cols: set[str]) -> None:
    """Recreate *table* without the columns in *drop_cols* (SQLite safe pattern)."""
    import tempfile

    bind = op.get_bind()
    cols = _get_columns_to_keep(bind, table, drop_cols)
    if len(cols) == bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall().__len__():
        return  # nothing to drop

    col_list = ", ".join(cols)
    new_table = f"{table}__downgrade_tmp"

    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {new_table}"))

    # Create new table with same schema minus dropped columns
    # by using CREATE TABLE ... AS SELECT to infer types
    op.execute(sa.text(f"CREATE TABLE {new_table} AS SELECT {col_list} FROM {table} WHERE 0"))
    op.execute(sa.text(f"INSERT INTO {new_table} ({col_list}) SELECT {col_list} FROM {table}"))
    op.execute(sa.text(f"DROP TABLE {table}"))
    op.execute(sa.text(f"ALTER TABLE {new_table} RENAME TO {table}"))

    op.execute(sa.text("PRAGMA foreign_keys=ON"))
