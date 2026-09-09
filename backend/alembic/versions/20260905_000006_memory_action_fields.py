"""Add queryable action-item semantics to memory facts.

Revision ID: 20260905_000006
Revises: 20260905_000005
"""

import sqlalchemy as sa

from alembic import op

revision = "20260905_000006"
down_revision = "20260905_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        str(row[1]) for row in bind.exec_driver_sql("PRAGMA table_info(user_memories)").fetchall()
    }
    for name, definition in (
        ("action_status", "TEXT"),
        ("assignee", "TEXT"),
        ("due_at", "TIMESTAMP"),
    ):
        if name not in existing:
            op.execute(sa.text(f"ALTER TABLE user_memories ADD COLUMN {name} {definition}"))

    version_existing = {
        str(row[1])
        for row in bind.exec_driver_sql("PRAGMA table_info(memory_fact_versions)").fetchall()
    }
    for name, definition in (
        ("action_status", "TEXT"),
        ("assignee", "TEXT"),
        ("due_at", "TIMESTAMP"),
    ):
        if name not in version_existing:
            op.execute(sa.text(f"ALTER TABLE memory_fact_versions ADD COLUMN {name} {definition}"))
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_memories_project_actions "
            "ON user_memories(user_id, project_id, fact_type, action_status, due_at)"
        )
    )


def downgrade() -> None:
    # Preserve domain/audit data during binary rollback.
    pass
