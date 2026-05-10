import sqlite3
from concurrent.futures import ThreadPoolExecutor

from src.core.database import SCHEMA_SQL
from src.core.database.meetings import create_meeting, create_meeting_file_if_absent


def test_create_meeting_file_if_absent_concurrent_same_hash(tmp_path):
    db_path = tmp_path / "upload-concurrency.db"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        meeting_id = create_meeting(conn, title="Concurrency Meeting", user_id="test")
        conn.commit()

    def _worker(i: int) -> int | None:
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            result = create_meeting_file_if_absent(
                conn,
                meeting_id=meeting_id,
                file_type="pdf",
                file_name=f"same-{i}.pdf",
                file_path=f"/tmp/same-{i}.pdf",
                content_hash="same-hash",
            )
            conn.commit()
            return result

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(_worker, range(20)))

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM meeting_files WHERE meeting_id=? AND content_hash=?",
            (meeting_id, "same-hash"),
        ).fetchone()
    assert row is not None
    assert row[0] == 1
