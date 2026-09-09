"""Version memory indexes and retain bounded retry state.

Revision ID: 20260905_000001
Revises: 20260904_000005
"""

import sqlalchemy as sa

from alembic import op

revision = "20260905_000001"
down_revision = "20260904_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from src.core.database._memory_schema import MEMORY_SEMANTICS_COLUMNS
    from src.core.database.conversation_state import CONVERSATION_STATE_SCHEMA_SQL

    for statement in CONVERSATION_STATE_SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(sa.text(statement))
    existing = {
        str(r[1]) for r in op.get_bind().exec_driver_sql("PRAGMA table_info(user_memories)")
    }
    for name in ("revision", "vector_attempts", "vector_retry_at"):
        if name not in existing:
            op.execute(
                sa.text(
                    f"ALTER TABLE user_memories ADD COLUMN {name} {MEMORY_SEMANTICS_COLUMNS[name]}"
                )
            )


def downgrade() -> None:
    # Additive metadata is safe for the previous binary; do not erase version
    # identity during an application rollback.
    pass
