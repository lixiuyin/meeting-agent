"""Recovery behavior tests for processing-start timestamps."""

from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_connection,
    get_write_connection,
)
from src.services.processor._recovery import recover_stale_meetings


def test_recovery_uses_processing_started_at_as_primary_timestamp(tmp_path):
    with get_write_connection() as conn:
        meeting_id = create_meeting(conn, title="recovery", description="", user_id="test")
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="x.txt",
            file_path=str(tmp_path / "x.txt"),
        )
        conn.execute(
            """
            UPDATE meetings
            SET status='processing',
                updated_at=datetime('now', '-1 day'),
                processing_started_at=datetime('now')
            WHERE id=?
            """,
            (meeting_id,),
        )
        conn.execute(
            """
            UPDATE meeting_files
            SET status='processing',
                updated_at=datetime('now', '-1 day'),
                processing_started_at=datetime('now')
            WHERE id=?
            """,
            (file_id,),
        )

    recovered = recover_stale_meetings()
    assert recovered == 0

    with get_connection() as conn:
        meeting = conn.execute("SELECT status FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        file = conn.execute("SELECT status FROM meeting_files WHERE id=?", (file_id,)).fetchone()
        assert meeting["status"] == "processing"
        assert file["status"] == "processing"


def test_recovery_falls_back_to_updated_at_when_started_at_missing(tmp_path):
    with get_write_connection() as conn:
        meeting_id = create_meeting(conn, title="fallback", description="", user_id="test")
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="y.txt",
            file_path=str(tmp_path / "y.txt"),
        )
        conn.execute(
            """
            UPDATE meetings
            SET status='processing',
                updated_at=datetime('now', '-1 day'),
                processing_started_at=NULL
            WHERE id=?
            """,
            (meeting_id,),
        )
        conn.execute(
            """
            UPDATE meeting_files
            SET status='processing',
                updated_at=datetime('now', '-1 day'),
                processing_started_at=NULL
            WHERE id=?
            """,
            (file_id,),
        )

    recovered = recover_stale_meetings()
    assert recovered == 1

    with get_connection() as conn:
        meeting = conn.execute(
            "SELECT status, processing_started_at FROM meetings WHERE id=?",
            (meeting_id,),
        ).fetchone()
        file = conn.execute(
            "SELECT status, processing_started_at FROM meeting_files WHERE id=?",
            (file_id,),
        ).fetchone()
        assert meeting["status"] == "failed"
        assert meeting["processing_started_at"] is None
        assert file["status"] == "error"
        assert file["processing_started_at"] is None
