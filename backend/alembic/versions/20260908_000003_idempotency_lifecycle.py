"""Persist idempotency lifecycle independently from encrypted responses."""

from sqlalchemy import inspect

from alembic import op

revision = "20260908_000003"
down_revision = "20260908_000002"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("idempotency_keys")}
    if "lifecycle_state" not in columns:
        op.execute(
            "ALTER TABLE idempotency_keys "
            "ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'legacy_unknown'"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_idempotency_lifecycle_expiry "
        "ON idempotency_keys(lifecycle_state, expires_at)"
    )


def downgrade():
    raise RuntimeError("Restore a verified backup to preserve idempotency recovery fences")
