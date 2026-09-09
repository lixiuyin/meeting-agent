"""Reconcile durable chat-run journal tables for already-upgraded databases.

Revision ID: 20260905_000003
Revises: 20260905_000002
"""

import sqlalchemy as sa

from alembic import op

revision = "20260905_000003"
down_revision = "20260905_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Some development databases reached the earlier revision before the
    # journal DDL was introduced. CREATE IF NOT EXISTS makes this a safe repair
    # for those databases and a no-op for clean installations.
    from src.core.database.conversation_state import CONVERSATION_STATE_SCHEMA_SQL

    for statement in CONVERSATION_STATE_SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(sa.text(statement))


def downgrade() -> None:
    # Run identity and replay data remain readable by the previous binary.
    # Do not destroy recovery/audit evidence during a code rollback.
    pass
