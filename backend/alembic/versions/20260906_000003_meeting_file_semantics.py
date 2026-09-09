"""Add editable meeting-material role and approval status.

Revision ID: 20260906_000003
Revises: 20260906_000002
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260906_000003"
down_revision = "20260906_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {str(row[1]) for row in bind.exec_driver_sql("PRAGMA table_info(meeting_files)")}
    if "material_role" not in existing:
        op.add_column("meeting_files", sa.Column("material_role", sa.Text(), nullable=True))
    if "approval_status" not in existing:
        op.add_column(
            "meeting_files",
            sa.Column(
                "approval_status",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'unreviewed'"),
            ),
        )


def downgrade() -> None:
    pass
