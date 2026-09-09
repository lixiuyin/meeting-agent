import json

from src.core import database as db
from src.core.database.extraction_progress import extraction_progress
from src.core.source_revision_fence import meeting_file_source_token


def test_progress_requires_current_complete_windows_and_owner(db_conn):
    conn = db_conn
    meeting = db.create_meeting(conn, title="Review", user_id="alice")
    file_id = db.create_meeting_file(
        conn,
        meeting_id=meeting,
        user_id="alice",
        file_type="txt",
        file_name="minutes.txt",
        file_path="owned.txt",
    )
    conn.execute(
        "UPDATE meeting_files SET status='ready',material_role='minutes' WHERE id=?", (file_id,)
    )
    file = dict(conn.execute("SELECT * FROM meeting_files WHERE id=?", (file_id,)).fetchone())
    assert extraction_progress(conn, "alice")["unknown"] == 1
    assert sum(extraction_progress(conn, "bob").values()) == 0
    payload = {
        "user_id": "alice",
        "file_ids": [file_id],
        "source_file_revision": meeting_file_source_token(file),
        "source_window_count": 2,
        "source_window_start": 0,
    }
    for index in range(2):
        conn.execute(
            "INSERT INTO durable_jobs(id,kind,dedupe_key,payload_json,status) VALUES (?, 'fact_extraction', ?, ?, 'completed')",
            (str(index), str(index), json.dumps({**payload, "source_window_start": index * 100})),
        )
        progress = extraction_progress(conn, "alice", meeting_id=meeting)
        assert progress["completed" if index else "unknown"] == 1
    conn.execute(
        "UPDATE meeting_files SET source_revision=source_revision+1 WHERE id=?", (file_id,)
    )
    assert extraction_progress(conn, "alice")["unknown"] == 1


def test_reference_material_is_not_presented_as_extraction_complete(db_conn):
    meeting = db.create_meeting(db_conn, title="Reading", user_id="alice")
    file_id = db.create_meeting_file(
        db_conn,
        meeting_id=meeting,
        user_id="alice",
        file_type="pdf",
        file_name="textbook.pdf",
        file_path="owned.pdf",
    )
    progress = extraction_progress(db_conn, "alice")
    assert progress["reference"] == 1
    assert progress["completed"] == 0
    assert progress["held_for_source_review"] == 0

    db.set_memory(
        db_conn,
        user_id="alice",
        key="course.reference.pending",
        value="Reference fact",
        source="auto_extracted",
        assertion_status="pending",
        meeting_ids=[meeting],
        file_ids=[file_id],
        evidence_refs=[{"meeting_id": meeting, "file_id": file_id}],
    )
    assert extraction_progress(db_conn, "alice")["held_for_source_review"] == 1
    assert extraction_progress(db_conn, "alice", meeting_id=meeting)["held_for_source_review"] == 1
