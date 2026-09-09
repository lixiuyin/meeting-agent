"""Add durable-job successors and native index health state.

Revision ID: 20260904_000001
Revises: 20260903_000006
Create Date: 2026-09-04 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260904_000001"
down_revision = "20260903_000006"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {
        str(row[1])
        for row in op.get_bind().exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    }


def upgrade() -> None:
    job_columns = _columns("durable_jobs")
    if "rerun_requested" not in job_columns:
        op.execute(
            sa.text(
                "ALTER TABLE durable_jobs ADD COLUMN rerun_requested "
                "INTEGER NOT NULL DEFAULT 0 CHECK(rerun_requested IN (0, 1))"
            )
        )
    if "next_payload_json" not in job_columns:
        op.execute(sa.text("ALTER TABLE durable_jobs ADD COLUMN next_payload_json TEXT"))

    index_columns = _columns("index_state")
    if "bm25_indexed_at" not in index_columns:
        op.execute(sa.text("ALTER TABLE index_state ADD COLUMN bm25_indexed_at TIMESTAMP"))
    if "native_status" not in index_columns:
        op.execute(
            sa.text(
                "ALTER TABLE index_state ADD COLUMN native_status TEXT NOT NULL "
                "DEFAULT 'unknown' CHECK(native_status IN ('unknown','building','ready','failed'))"
            )
        )
    if "native_last_error" not in index_columns:
        op.execute(sa.text("ALTER TABLE index_state ADD COLUMN native_last_error TEXT"))


def downgrade() -> None:
    for table, columns in (
        ("index_state", ("native_last_error", "native_status", "bm25_indexed_at")),
        ("durable_jobs", ("next_payload_json", "rerun_requested")),
    ):
        existing = _columns(table)
        for column in columns:
            if column in existing:
                op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {column}"))
