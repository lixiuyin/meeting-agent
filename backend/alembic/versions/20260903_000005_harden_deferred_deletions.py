"""Harden deferred deletion ownership and leasing.

Revision ID: 20260903_000005
Revises: 20260903_000004
Create Date: 2026-09-03 22:20:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260903_000005"
down_revision = "20260903_000004"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.execute(sa.text(f"PRAGMA table_info({table})"))}


def upgrade() -> None:
    if "lease_owner" not in _columns("pending_vector_deletions"):
        op.add_column("pending_vector_deletions", sa.Column("lease_owner", sa.Text()))
    if "lease_expires_at" not in _columns("pending_vector_deletions"):
        op.add_column("pending_vector_deletions", sa.Column("lease_expires_at", sa.DateTime()))
    if "user_id" not in _columns("account_deletion_requests"):
        op.add_column("account_deletion_requests", sa.Column("user_id", sa.Text()))

    op.execute(
        sa.text(
            "DELETE FROM pending_vector_deletions WHERE id NOT IN ("
            "SELECT MIN(id) FROM pending_vector_deletions "
            "GROUP BY collection, embedding_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_vec_resource_unique "
            "ON pending_vector_deletions(collection, embedding_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_pending_vec_lease "
            "ON pending_vector_deletions(status, lease_expires_at)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_account_deletion_user "
            "ON account_deletion_requests(user_id, created_at)"
        )
    )
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO schema_version (version, description) VALUES "
            "(52, 'Harden deferred deletion ownership and leasing')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_account_deletion_user"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_pending_vec_lease"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_pending_vec_resource_unique"))
    with op.batch_alter_table("account_deletion_requests") as batch_op:
        batch_op.drop_column("user_id")
    with op.batch_alter_table("pending_vector_deletions") as batch_op:
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_owner")
    op.execute(sa.text("DELETE FROM schema_version WHERE version = 52"))
