"""Add typed memory lifecycle state and immutable fact versions.

Revision ID: 20260905_000004
Revises: 20260905_000003
"""

import sqlalchemy as sa

from alembic import op

revision = "20260905_000004"
down_revision = "20260905_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from src.core.database._memory_schema import (
        MEMORY_FACT_VERSIONS_SCHEMA_SQL,
        MEMORY_SEMANTICS_COLUMNS,
    )

    existing = {
        str(row[1])
        for row in op.get_bind().exec_driver_sql("PRAGMA table_info(user_memories)").fetchall()
    }
    for name in (
        "fact_type",
        "assertion_status",
        "project_id",
        "subject",
        "predicate",
        "object_value",
        "retracted_at",
    ):
        if name not in existing:
            op.execute(
                sa.text(
                    f"ALTER TABLE user_memories ADD COLUMN {name} {MEMORY_SEMANTICS_COLUMNS[name]}"
                )
            )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_memories_lifecycle "
            "ON user_memories(user_id, assertion_status, fact_type, project_id)"
        )
    )
    for statement in MEMORY_FACT_VERSIONS_SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(sa.text(statement))


def downgrade() -> None:
    # Lifecycle history is operational/audit evidence and remains readable by
    # the previous binary.  Do not destroy it during code rollback.
    pass
