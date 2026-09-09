"""Backfill a baseline fact snapshot for memories created before versioning.

Revision ID: 20260906_000002
Revises: 20260906_000001
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260906_000002"
down_revision = "20260906_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """INSERT OR IGNORE INTO memory_fact_versions (
                   memory_id, user_id, memory_key, revision, value, source,
                   fact_type, assertion_status, project_id, subject, predicate,
                   object_value, action_status, assignee, due_at, category,
                   confidence, valid_from, valid_to, evidence_message_ids,
                   evidence_excerpt, evidence_refs, conflicts_with, meeting_ids,
                   file_ids, recorded_at, recorded_to
               )
               SELECT
                   m.id, m.user_id, m.key, m.revision, m.value, m.source,
                   m.fact_type, m.assertion_status, m.project_id, m.subject, m.predicate,
                   m.object_value, m.action_status, m.assignee, m.due_at, m.category,
                   m.confidence, m.valid_from, m.valid_to, m.evidence_message_ids,
                   m.evidence_excerpt, m.evidence_refs, m.conflicts_with,
                   (SELECT GROUP_CONCAT(scope_id)
                      FROM memory_scopes
                     WHERE memory_id=m.id AND scope_type='meeting'),
                   (SELECT GROUP_CONCAT(scope_id)
                      FROM memory_scopes
                     WHERE memory_id=m.id AND scope_type='file'),
                   COALESCE(m.updated_at, m.created_at, CURRENT_TIMESTAMP), NULL
                 FROM user_memories AS m
                WHERE NOT EXISTS (
                    SELECT 1 FROM memory_fact_versions AS v WHERE v.memory_id=m.id
                )"""
        )
    )


def downgrade() -> None:
    # The backfilled rows are audit history and cannot be distinguished safely
    # from snapshots created by an older application process. Retain them.
    pass
