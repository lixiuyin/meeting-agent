from src.core import database as db
from src.core.memory_admission import (
    explicitly_requested_memory,
    file_memory_policy,
    is_reference_memory,
    reference_memory_sql,
)


def test_reference_materials_are_not_auto_promoted():
    assert file_memory_policy({"file_type": "pdf"}, "Lecture 8.pdf") == "knowledge_only"
    assert file_memory_policy({"material_role": "minutes"}) == "project_state"
    assert (
        file_memory_policy({"material_role": "minutes", "approval_status": "rejected"})
        == "disabled"
    )


def test_existing_reference_facts_remain_saved_but_are_not_personal_context():
    row = {"key": "topic.chatgpt.release_date", "source": "auto_extracted", "fact_type": "fact"}
    assert is_reference_memory(row)
    assert not is_reference_memory({**row, "source": "manual"})
    assert not is_reference_memory({**row, "category": "explicit_memory"})
    assert explicitly_requested_memory("请记住这个时间")
    assert not explicitly_requested_memory("do not remember this")
    assert not explicitly_requested_memory("不要记住这个时间")


def test_legacy_project_fact_reference_is_classified_as_reference() -> None:
    row = {
        "key": "course.stat8307.assessment",
        "source": "auto_extracted",
        "fact_type": "project_fact",
    }

    assert is_reference_memory(row)
    assert not is_reference_memory({**row, "project_id": "active-project"})


def test_document_semantics_override_legacy_project_labels() -> None:
    user_id = "reference-source-policy"
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Course notes", user_id=user_id)
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="pdf",
            file_name="lecture.pdf",
            file_path="/tmp/lecture.pdf",
            user_id=user_id,
        )
        db.update_meeting_file_semantics(
            conn,
            file_id,
            material_role="attachment",
            business_domain="course",
            user_id=user_id,
        )
        db.set_memory(
            conn,
            user_id=user_id,
            key="project.legacy.reference_value",
            value="A fact copied from lecture material",
            source="auto_extracted",
            fact_type="project_fact",
            project_id="legacy-project",
            evidence_refs=[{"meeting_id": meeting_id, "file_id": file_id}],
        )
        row = db.get_memory_full(conn, user_id=user_id, key="project.legacy.reference_value")
        assert row is not None
        assert is_reference_memory(row, conn=conn)
        classified = conn.execute(
            "SELECT " + reference_memory_sql() + " FROM user_memories m WHERE m.id=?",
            (row["id"],),
        ).fetchone()
        assert classified[0] == 1

        personal = db.list_memories(conn, user_id=user_id, memory_kind="personal")
        reference = db.list_memories(conn, user_id=user_id, memory_kind="reference")
        assert row["key"] not in {item["key"] for item in personal}
        assert row["key"] in {item["key"] for item in reference}

        conn.execute(
            "UPDATE user_memories SET evidence_refs=? WHERE id=?",
            ('["legacy-string",42]', row["id"]),
        )
        malformed = conn.execute(
            "SELECT " + reference_memory_sql() + " FROM user_memories m WHERE m.id=?",
            (row["id"],),
        ).fetchone()
        assert malformed[0] == 0
