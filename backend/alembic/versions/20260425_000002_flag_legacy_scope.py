"""Flag pre-scope memories/entities as legacy so they don't pollute scoped queries.

Revision ID: 20260425_000002
Revises: 20260425_000001
Create Date: 2026-04-25 01:21:00

Matches legacy migration v29. Adds ``is_legacy_scope`` and backfills it to 1
for rows that predate the scope columns — they have no ``meeting_ids`` /
``file_ids`` and should not leak into meeting-scoped chat queries.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260425_000002"
down_revision = "20260425_000001"
branch_labels = None
depends_on = None


_LEGACY_COLUMN_TABLES = ("user_memories", "memory_entities")
_BACKFILL_STATEMENTS = (
    "UPDATE user_memories SET is_legacy_scope = 1 WHERE meeting_ids IS NULL AND file_ids IS NULL",
    "UPDATE memory_entities SET is_legacy_scope = 1 WHERE meeting_ids IS NULL AND file_ids IS NULL",
)


def _has_column(bind, table: str, column: str) -> bool:
    """Check column existence via PRAGMA so we don't rely on exception text."""
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _LEGACY_COLUMN_TABLES:
        if _has_column(bind, table, "is_legacy_scope"):
            continue
        op.execute(
            sa.text(f"ALTER TABLE {table} ADD COLUMN is_legacy_scope INTEGER NOT NULL DEFAULT 0")
        )
    for stmt in _BACKFILL_STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    # Drop is_legacy_scope columns via recreate pattern
    for table in _LEGACY_COLUMN_TABLES:
        _recreate_without_columns(table, {"is_legacy_scope"})


def _recreate_without_columns(table: str, drop_cols: set[str]) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    cols = [row[1] for row in rows if row[1] not in drop_cols]
    if len(cols) == len(rows):
        return

    col_list = ", ".join(cols)
    new_table = f"{table}__downgrade_tmp"

    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {new_table}"))
    op.execute(sa.text(f"CREATE TABLE {new_table} AS SELECT {col_list} FROM {table} WHERE 0"))
    op.execute(sa.text(f"INSERT INTO {new_table} ({col_list}) SELECT {col_list} FROM {table}"))
    op.execute(sa.text(f"DROP TABLE {table}"))
    op.execute(sa.text(f"ALTER TABLE {new_table} RENAME TO {table}"))
    op.execute(sa.text("PRAGMA foreign_keys=ON"))
