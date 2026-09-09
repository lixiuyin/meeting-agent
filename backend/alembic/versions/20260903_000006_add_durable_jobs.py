"""Add the durable background-job queue.

Revision ID: 20260903_000006
Revises: 20260903_000005
Create Date: 2026-09-03 23:30:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from src.core.database._migration_helpers import split_sql_statements
from src.core.database.jobs import JOBS_SCHEMA_SQL

revision = "20260903_000006"
down_revision = "20260903_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in split_sql_statements(JOBS_SCHEMA_SQL):
        if statement.strip():
            op.execute(sa.text(statement))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_durable_jobs_lease"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_durable_jobs_claim"))
    op.execute(sa.text("DROP TABLE IF EXISTS durable_jobs"))
