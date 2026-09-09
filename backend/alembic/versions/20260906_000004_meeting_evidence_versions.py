"""Add monotonic meeting evidence versions and semantic history.

Revision ID: 20260906_000004
Revises: 20260906_000003
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260906_000004"
down_revision = "20260906_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {str(row[1]) for row in bind.exec_driver_sql("PRAGMA table_info(meeting_files)")}
    if "source_revision" not in existing:
        op.add_column(
            "meeting_files",
            sa.Column("source_revision", sa.Integer(), nullable=False, server_default="1"),
        )
    if "content_recorded_at" not in existing:
        op.add_column("meeting_files", sa.Column("content_recorded_at", sa.DateTime()))
    if "semantic_updated_at" not in existing:
        op.add_column("meeting_files", sa.Column("semantic_updated_at", sa.DateTime()))
    if "approval_reason" not in existing:
        op.add_column("meeting_files", sa.Column("approval_reason", sa.Text()))
    bind.exec_driver_sql(
        "UPDATE meeting_files SET content_recorded_at="
        "COALESCE(content_recorded_at, created_at, CURRENT_TIMESTAMP)"
    )
    table_exists = bind.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meeting_file_semantic_events'"
    ).fetchone()
    if table_exists is None:
        op.create_table(
            "meeting_file_semantic_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "file_id",
                sa.Integer(),
                sa.ForeignKey("meeting_files.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_id", sa.Text(), nullable=False),
            sa.Column("source_revision", sa.Integer(), nullable=False),
            sa.Column("material_role", sa.Text(), nullable=False),
            sa.Column("approval_status", sa.Text(), nullable=False),
            sa.Column("approval_reason", sa.Text()),
            sa.Column(
                "changed_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
    index_exists = bind.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='idx_meeting_file_semantic_events_file'"
    ).fetchone()
    if index_exists is None:
        op.create_index(
            "idx_meeting_file_semantic_events_file",
            "meeting_file_semantic_events",
            ["file_id", "source_revision"],
        )


def downgrade() -> None:
    op.drop_index(
        "idx_meeting_file_semantic_events_file",
        table_name="meeting_file_semantic_events",
    )
    op.drop_table("meeting_file_semantic_events")
