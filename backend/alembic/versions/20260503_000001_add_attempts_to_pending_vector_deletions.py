"""Add attempts column to pending_vector_deletions for retry tracking.

Revision ID: 20260503_000001
Revises: 20260429_000002
Create Date: 2026-05-03 13:00:00

Adds an `attempts` counter column so cleanup_pending_vector_deletions() can
track how many times a deletion has been retried and give up permanently after
a configured threshold (instead of retrying forever).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260503_000001"
down_revision = "20260429_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = bind.execute(sa.text("PRAGMA table_info(pending_vector_deletions)")).fetchall()
    col_names = [row[1] for row in cols]
    if "attempts" in col_names:
        return  # idempotent
    op.execute(
        sa.text(
            "ALTER TABLE pending_vector_deletions ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    cols = bind.execute(sa.text("PRAGMA table_info(pending_vector_deletions)")).fetchall()
    col_names = [row[1] for row in cols]
    if "attempts" not in col_names:
        return

    # SQLite < 3.35 lacks DROP COLUMN; use recreate pattern
    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.execute(sa.text("DROP TABLE IF EXISTS pending_vector_deletions_new"))

    op.execute(
        sa.text(
            "CREATE TABLE pending_vector_deletions_new ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "collection TEXT NOT NULL, "
            "embedding_id TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO pending_vector_deletions_new "
            "(id, collection, embedding_id, created_at) "
            "SELECT id, collection, embedding_id, created_at "
            "FROM pending_vector_deletions"
        )
    )
    op.execute(sa.text("DROP TABLE pending_vector_deletions"))
    op.execute(
        sa.text("ALTER TABLE pending_vector_deletions_new RENAME TO pending_vector_deletions")
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_pending_vec_collection "
            "ON pending_vector_deletions(collection)"
        )
    )

    op.execute(sa.text("PRAGMA foreign_keys=ON"))
