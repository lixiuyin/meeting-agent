"""Persist the retrieval configuration required to resume a session safely.

Revision ID: 20260905_000002
Revises: 20260905_000001
"""

import sqlalchemy as sa

from alembic import op

revision = "20260905_000002"
down_revision = "20260905_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(sa.Column("config_json", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("config_version", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_column("config_version")
        batch.drop_column("config_json")
