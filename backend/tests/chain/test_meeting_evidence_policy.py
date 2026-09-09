"""Retrieval governance for reviewed and system-time-scoped meeting evidence."""

import datetime

from src.core import database as db
from src.services.chain._retrieve_filters import apply_meeting_evidence_policy


def _file_doc(file_id: int) -> list[dict]:
    return [
        {
            "content": "The release was approved.",
            "metadata": {
                "file_id": file_id,
                "material_role": "transcript",
                "approval_status": "approved",
            },
            "score": 0.9,
        }
    ]


def test_current_semantics_override_stale_index_metadata_and_rejected_fails_closed():
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Review", user_id="u1")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="decision.txt",
            file_path="/tmp/decision.txt",
            user_id="u1",
        )
        db.update_meeting_file_semantics(
            conn,
            file_id,
            material_role="decision_log",
            approval_status="rejected",
            approval_reason="Superseded proposal",
            user_id="u1",
        )

    docs = _file_doc(file_id)
    assert apply_meeting_evidence_policy(docs, query="What was approved?", user_id="u1") == []

    audited = apply_meeting_evidence_policy(
        docs,
        query="Why was this rejected?",
        user_id="u1",
    )
    assert audited[0]["metadata"]["material_role"] == "decision_log"
    assert audited[0]["metadata"]["approval_status"] == "rejected"
    assert audited[0]["metadata"]["approval_reason"] == "Superseded proposal"
    assert audited[0]["metadata"]["file_source_revision"] == 2


def test_known_at_excludes_documents_not_yet_recorded():
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Late evidence", user_id="u1")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="late.txt",
            file_path="/tmp/late.txt",
            user_id="u1",
        )

    result = apply_meeting_evidence_policy(
        _file_doc(file_id),
        query="What was known?",
        user_id="u1",
        known_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
    )

    assert result == []


def test_unknown_or_cross_tenant_file_is_not_usable_evidence():
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Private", user_id="u2")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="private.txt",
            file_path="/tmp/private.txt",
            user_id="u2",
        )

    assert (
        apply_meeting_evidence_policy(_file_doc(file_id), query="What was approved?", user_id="u1")
        == []
    )


def test_stale_index_generation_is_not_usable_while_successor_rebuilds():
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Revision", user_id="u1")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="revision.txt",
            file_path="/tmp/revision.txt",
            user_id="u1",
        )
        db.update_meeting_file_semantics(
            conn,
            file_id,
            approval_status="approved",
            user_id="u1",
        )

    stale = _file_doc(file_id)
    stale[0]["metadata"]["file_source_revision"] = 1
    current = _file_doc(file_id)
    current[0]["metadata"]["file_source_revision"] = 2

    assert apply_meeting_evidence_policy(stale, query="What was approved?", user_id="u1") == []
    assert (
        len(apply_meeting_evidence_policy(current, query="What was approved?", user_id="u1")) == 1
    )
