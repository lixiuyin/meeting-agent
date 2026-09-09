"""Add immutable source-window references to memory facts.

Revision ID: 20260905_000007
Revises: 20260905_000006
"""

import sqlalchemy as sa

from alembic import op

revision = "20260905_000007"
down_revision = "20260905_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("user_memories", "memory_fact_versions"):
        existing = {
            str(row[1])
            for row in bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        }
        if "evidence_refs" not in existing:
            op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN evidence_refs TEXT"))


def downgrade() -> None:
    # Evidence is audit data and is intentionally preserved on binary rollback.
    pass
