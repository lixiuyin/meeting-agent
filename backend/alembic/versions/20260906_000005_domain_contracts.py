"""Project registry, material domain and bounded fact query fingerprints."""

from alembic import op
from src.core.database._domain_schema import DOMAIN_STATEMENTS

revision = "20260906_000005"
down_revision = "20260906_000004"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in ("meeting_files",):
        columns = {row[1] for row in bind.exec_driver_sql(f"PRAGMA table_info({table})")}
        if "business_domain" not in columns:
            bind.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN business_domain "
                "TEXT NOT NULL DEFAULT 'unspecified'"
            )
    for statement in DOMAIN_STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade():
    # Preserving project mappings and material classification is intentional.
    raise RuntimeError("Data-preserving migration; restore a verified backup to downgrade")
