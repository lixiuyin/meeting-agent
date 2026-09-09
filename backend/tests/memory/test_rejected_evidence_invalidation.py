"""Automatic memories follow meeting evidence approval lifecycle."""

from src.core import database as db


def test_auto_memory_retracts_only_after_all_file_support_is_rejected() -> None:
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Review", user_id="u1")
        first_file = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="first.txt",
            file_path="/tmp/first.txt",
            user_id="u1",
        )
        second_file = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="second.txt",
            file_path="/tmp/second.txt",
            user_id="u1",
        )
        db.set_memory(
            conn,
            user_id="u1",
            key="decision.release",
            value="Release Friday",
            source="auto_extracted",
            meeting_ids=[meeting_id],
            file_ids=[first_file, second_file],
        )
        db.update_meeting_file_semantics(
            conn,
            first_file,
            approval_status="rejected",
            approval_reason="Incorrect transcript",
            user_id="u1",
        )
        assert (
            db.retract_memories_with_only_rejected_file_evidence(
                conn, user_id="u1", file_id=first_file
            )
            == []
        )
        still_active = db.get_memory_full(conn, user_id="u1", key="decision.release")
        assert still_active is not None
        assert still_active["assertion_status"] == "confirmed"

        db.update_meeting_file_semantics(
            conn,
            second_file,
            approval_status="rejected",
            approval_reason="Superseded notes",
            user_id="u1",
        )
        assert db.retract_memories_with_only_rejected_file_evidence(
            conn, user_id="u1", file_id=second_file
        ) == ["decision.release"]
        retracted = db.get_memory_full(conn, user_id="u1", key="decision.release")

    assert retracted is not None
    assert retracted["assertion_status"] == "retracted"
    assert retracted["vector_state"] == "inactive"
