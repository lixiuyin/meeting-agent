"""Separate memory salience, freshness, confidence, usefulness, and evidence.

Revision ID: 20260904_000003
Revises: 20260904_000002
Create Date: 2026-09-04 17:30:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260904_000003"
down_revision = "20260904_000002"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        str(row[1])
        for row in op.get_bind().exec_driver_sql("PRAGMA table_info(user_memories)").fetchall()
    }


def upgrade() -> None:
    from src.core.database._memory_schema import MEMORY_SEMANTICS_COLUMNS

    existing = _columns()
    for name, definition in MEMORY_SEMANTICS_COLUMNS.items():
        if name not in existing:
            op.execute(sa.text(f"ALTER TABLE user_memories ADD COLUMN {name} {definition}"))
    op.execute(sa.text("UPDATE user_memories SET salience=importance WHERE salience IS NULL"))
    op.execute(
        sa.text(
            "UPDATE user_memories SET last_confirmed_at=COALESCE(updated_at, created_at) "
            "WHERE last_confirmed_at IS NULL"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_memories_user_salience "
            "ON user_memories(user_id, salience DESC) WHERE superseded_by IS NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_memories_user_salience"))
    existing = _columns()
    for name in (
        "conflicts_with",
        "evidence_excerpt",
        "evidence_message_ids",
        "valid_to",
        "valid_from",
        "last_confirmed_at",
        "usefulness_count",
        "usefulness_score",
        "freshness_score",
        "confidence",
        "salience",
    ):
        if name in existing:
            op.execute(sa.text(f"ALTER TABLE user_memories DROP COLUMN {name}"))
