"""Preserve fact history during recall maintenance; track profile sources."""

from alembic import op

revision = "20260907_000001"
down_revision = "20260906_000007"
branch_labels = None
depends_on = None


def upgrade():
    from src.core.database.memory_lifecycle import ensure_lifecycle_schema

    ensure_lifecycle_schema(op.get_bind().connection.driver_connection)


def downgrade():
    raise RuntimeError("Restore a verified backup; archive/provenance data is preserved")
