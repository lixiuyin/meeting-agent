"""Add observable account deletion batches.

Revision ID: 20260903_000004
Revises: 20260903_000003
Create Date: 2026-09-03 21:20:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260903_000004"
down_revision = "20260903_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS account_deletion_requests ("
            "id TEXT PRIMARY KEY, idempotency_key_hash TEXT NOT NULL UNIQUE, "
            "total_jobs INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    columns = {
        row[1] for row in bind.execute(sa.text("PRAGMA table_info(pending_vector_deletions)"))
    }
    if "deletion_batch_id" not in columns:
        op.execute(
            sa.text(
                "ALTER TABLE pending_vector_deletions ADD COLUMN deletion_batch_id TEXT "
                "REFERENCES account_deletion_requests(id) ON DELETE SET NULL"
            )
        )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_pending_vec_deletion_batch "
            "ON pending_vector_deletions(deletion_batch_id, status)"
        )
    )
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO schema_version (version, description) VALUES "
            "(51, 'Add observable account deletion batches')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_pending_vec_deletion_batch"))
    with op.batch_alter_table("pending_vector_deletions") as batch_op:
        batch_op.drop_column("deletion_batch_id")
    op.execute(sa.text("DROP TABLE IF EXISTS account_deletion_requests"))
    op.execute(sa.text("DELETE FROM schema_version WHERE version = 51"))
