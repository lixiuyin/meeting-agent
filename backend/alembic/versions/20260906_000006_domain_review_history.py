"""Preserve material domain in immutable semantic review events."""

from alembic import op

revision = "20260906_000006"
down_revision = "20260906_000005"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {
        row[1] for row in bind.exec_driver_sql("PRAGMA table_info(meeting_file_semantic_events)")
    }
    if "business_domain" not in columns:
        bind.exec_driver_sql(
            "ALTER TABLE meeting_file_semantic_events ADD COLUMN business_domain "
            "TEXT NOT NULL DEFAULT 'unspecified'"
        )


def downgrade():
    raise RuntimeError("Data-preserving migration; restore a verified backup to downgrade")
