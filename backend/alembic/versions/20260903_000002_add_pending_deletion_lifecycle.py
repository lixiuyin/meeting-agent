"""Add lifecycle fields to pending vector deletion jobs.

Revision ID: 20260903_000002
Revises: 20260903_000001
Create Date: 2026-09-03 19:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260903_000002"
down_revision = "20260903_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        row[1] for row in bind.execute(sa.text("PRAGMA table_info(pending_vector_deletions)"))
    }
    if "status" not in columns:
        op.execute(
            sa.text(
                "ALTER TABLE pending_vector_deletions ADD COLUMN status TEXT "
                "NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'dead_letter'))"
            )
        )
    if "last_error" not in columns:
        op.execute(sa.text("ALTER TABLE pending_vector_deletions ADD COLUMN last_error TEXT"))
    if "updated_at" not in columns:
        op.execute(sa.text("ALTER TABLE pending_vector_deletions ADD COLUMN updated_at TIMESTAMP"))
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_pending_vec_status "
            "ON pending_vector_deletions(status, attempts, created_at)"
        )
    )
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO schema_version (version, description) VALUES "
            "(50, 'Add lifecycle fields to pending vector deletion jobs')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_pending_vec_status"))
    op.execute(sa.text("DELETE FROM schema_version WHERE version = 50"))
    # SQLite column removal would require a destructive table rebuild. Keeping
    # nullable compatibility columns is safer for a downgrade.
