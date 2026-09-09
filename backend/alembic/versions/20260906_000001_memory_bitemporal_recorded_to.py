"""Add system-time closure to immutable memory fact versions.

Revision ID: 20260906_000001
Revises: 20260905_000007
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260906_000001"
down_revision = "20260905_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        str(row[1])
        for row in bind.exec_driver_sql("PRAGMA table_info(memory_fact_versions)").fetchall()
    }
    if "recorded_to" not in columns:
        op.execute(sa.text("ALTER TABLE memory_fact_versions ADD COLUMN recorded_to TIMESTAMP"))
    op.execute(
        sa.text(
            """UPDATE memory_fact_versions AS current
                  SET recorded_to = (
                      SELECT MIN(next.recorded_at)
                        FROM memory_fact_versions AS next
                       WHERE next.memory_id = current.memory_id
                         AND next.revision > current.revision
                  )
                WHERE recorded_to IS NULL
                  AND EXISTS (
                      SELECT 1 FROM memory_fact_versions AS next
                       WHERE next.memory_id = current.memory_id
                         AND next.revision > current.revision
                  )"""
        )
    )


def downgrade() -> None:
    # System-time audit history is retained across binary rollback.
    pass
