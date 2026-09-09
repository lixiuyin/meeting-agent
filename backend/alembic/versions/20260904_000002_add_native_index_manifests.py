"""Add native index generation manifests and repair state.

Revision ID: 20260904_000002
Revises: 20260904_000001
Create Date: 2026-09-04 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260904_000002"
down_revision = "20260904_000001"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {
        str(row[1])
        for row in op.get_bind().exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    }


def upgrade() -> None:
    existing = _columns("index_state")
    additions = {
        "native_generation": "TEXT",
        "native_config_fingerprint": "TEXT",
        "chroma_chunk_count": "INTEGER",
        "bm25_chunk_count": "INTEGER",
        "native_manifest_checksum": "TEXT",
        "repair_pending": ("INTEGER NOT NULL DEFAULT 0 CHECK(repair_pending IN (0, 1))"),
    }
    for name, definition in additions.items():
        if name not in existing:
            op.execute(sa.text(f"ALTER TABLE index_state ADD COLUMN {name} {definition}"))


def downgrade() -> None:
    existing = _columns("index_state")
    for name in (
        "repair_pending",
        "native_manifest_checksum",
        "bm25_chunk_count",
        "chroma_chunk_count",
        "native_config_fingerprint",
        "native_generation",
    ):
        if name in existing:
            op.execute(sa.text(f"ALTER TABLE index_state DROP COLUMN {name}"))
