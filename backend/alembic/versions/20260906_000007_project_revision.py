"""Fence concurrent project edits without changing user bindings."""

from alembic import op

revision = "20260906_000007"
down_revision = "20260906_000006"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {r[1] for r in bind.exec_driver_sql("PRAGMA table_info(projects)")}
    if "revision" not in columns:
        bind.exec_driver_sql("ALTER TABLE projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")


def downgrade():
    raise RuntimeError("Restore a verified backup; project revision history is preserved")
