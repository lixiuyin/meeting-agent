"""Add evidence and validity to knowledge-graph relations.

Revision ID: 20260904_000005
Revises: 20260904_000004
"""

import sqlalchemy as sa

from alembic import op

revision = "20260904_000005"
down_revision = "20260904_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from src.core.database._relation_evidence_schema import RELATION_EVIDENCE_COLUMNS

    existing = {
        str(row[1]) for row in op.get_bind().exec_driver_sql("PRAGMA table_info(memory_relations)")
    }
    for name, definition in RELATION_EVIDENCE_COLUMNS.items():
        if name not in existing:
            op.execute(sa.text(f"ALTER TABLE memory_relations ADD COLUMN {name} {definition}"))
    op.execute(
        sa.text(
            "UPDATE memory_relations "
            "SET updated_at=COALESCE(created_at, CURRENT_TIMESTAMP) "
            "WHERE updated_at IS NULL"
        )
    )


def downgrade() -> None:
    existing = {
        str(row[1]) for row in op.get_bind().exec_driver_sql("PRAGMA table_info(memory_relations)")
    }
    for name in ("updated_at", "valid_to", "valid_from", "evidence_message_ids"):
        if name in existing:
            op.execute(sa.text(f"ALTER TABLE memory_relations DROP COLUMN {name}"))
