"""Persist structured task state for reliable session continuation.

Revision ID: 20260905_000005
Revises: 20260905_000004
"""

import sqlalchemy as sa

from alembic import op

revision = "20260905_000005"
down_revision = "20260905_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        str(row[1])
        for row in op.get_bind().exec_driver_sql("PRAGMA table_info(chat_sessions)").fetchall()
    }
    with op.batch_alter_table("chat_sessions") as batch:
        if "task_state_json" not in existing:
            batch.add_column(sa.Column("task_state_json", sa.Text(), nullable=True))
        if "task_state_version" not in existing:
            batch.add_column(
                sa.Column("task_state_version", sa.Integer(), nullable=False, server_default="1")
            )


def downgrade() -> None:
    # Preserve continuation state during code rollback.
    pass
