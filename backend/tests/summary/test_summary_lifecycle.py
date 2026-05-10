"""Tests for summary lifecycle: Ready → Searchable status transitions."""

import pytest

from src.core.database._migrations import _MIGRATIONS
from src.core.database.meetings import (
    clear_file_summary,
    clear_meeting_summary,
)


@pytest.fixture
def db_conn(tmp_path):
    """Provide an in-memory SQLite connection with full schema."""
    import sqlite3

    from src.core.database._migrations import _apply_migration

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  description TEXT NOT NULL,"
        "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.commit()
    for version, description, sql in _MIGRATIONS:
        _apply_migration(conn, sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.commit()
    yield conn
    conn.close()


def _create_test_meeting(conn, meeting_id=1):
    conn.execute(
        "INSERT OR IGNORE INTO meetings (id, title, status) VALUES (?, 'Test Meeting', 'ready')",
        (meeting_id,),
    )
    conn.commit()


def _create_test_file(conn, file_id=1, meeting_id=1):
    _create_test_meeting(conn, meeting_id)
    conn.execute(
        "INSERT OR IGNORE INTO meeting_files (id, meeting_id, file_type, file_name, file_path, status) "
        "VALUES (?, ?, 'video', 'test.mp4', '/tmp/test.mp4', 'ready')",
        (file_id, meeting_id),
    )
    conn.commit()


class TestSummaryStatusMigrations:
    @pytest.mark.unit
    def test_meeting_files_has_summary_status_column(self, db_conn):
        row = db_conn.execute("PRAGMA table_info(meeting_files)").fetchall()
        column_names = {r["name"] for r in row}
        assert "summary_status" in column_names

    @pytest.mark.unit
    def test_meetings_has_summary_status_column(self, db_conn):
        row = db_conn.execute("PRAGMA table_info(meetings)").fetchall()
        column_names = {r["name"] for r in row}
        assert "summary_status" in column_names

    @pytest.mark.unit
    def test_default_summary_status_is_pending(self, db_conn):
        _create_test_meeting(db_conn)
        _create_test_file(db_conn)
        row = db_conn.execute("SELECT summary_status FROM meetings WHERE id=1").fetchone()
        assert row["summary_status"] == "pending"
        row = db_conn.execute("SELECT summary_status FROM meeting_files WHERE id=1").fetchone()
        assert row["summary_status"] == "pending"


class TestFileSummaryStatus:
    @pytest.mark.unit
    def test_update_to_ready(self, db_conn):
        _create_test_file(db_conn)
        db_conn.execute("UPDATE meeting_files SET summary_status='ready' WHERE id=1")
        db_conn.commit()
        row = db_conn.execute("SELECT summary_status FROM meeting_files WHERE id=1").fetchone()
        assert row["summary_status"] == "ready"

    @pytest.mark.unit
    def test_update_to_failed(self, db_conn):
        _create_test_file(db_conn)
        db_conn.execute("UPDATE meeting_files SET summary_status='failed' WHERE id=1")
        db_conn.commit()
        row = db_conn.execute("SELECT summary_status FROM meeting_files WHERE id=1").fetchone()
        assert row["summary_status"] == "failed"


class TestMeetingSummaryStatus:
    @pytest.mark.unit
    def test_update_to_ready(self, db_conn):
        _create_test_meeting(db_conn)
        db_conn.execute("UPDATE meetings SET summary_status='ready' WHERE id=1")
        db_conn.commit()
        row = db_conn.execute("SELECT summary_status FROM meetings WHERE id=1").fetchone()
        assert row["summary_status"] == "ready"


class TestClearSummary:
    @pytest.mark.unit
    def test_clear_file_summary(self, db_conn):
        _create_test_file(db_conn)
        db_conn.execute("UPDATE meeting_files SET summary='test summary' WHERE id=1")
        db_conn.commit()
        clear_file_summary(db_conn, 1)
        db_conn.commit()
        row = db_conn.execute("SELECT summary FROM meeting_files WHERE id=1").fetchone()
        assert row["summary"] is None

    @pytest.mark.unit
    def test_clear_meeting_summary(self, db_conn):
        _create_test_meeting(db_conn)
        import json

        db_conn.execute(
            "INSERT INTO meeting_summaries (meeting_id, summary, contributing_file_ids) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(meeting_id) DO UPDATE SET "
            "summary=excluded.summary, "
            "contributing_file_ids=excluded.contributing_file_ids, "
            "updated_at=CURRENT_TIMESTAMP",
            (1, "test meeting summary", json.dumps([])),
        )
        db_conn.commit()
        clear_meeting_summary(db_conn, 1)
        db_conn.commit()
        row = db_conn.execute("SELECT * FROM meeting_summaries WHERE meeting_id=1").fetchone()
        assert row is None
        # summary_status should be reset to pending
        row = db_conn.execute("SELECT summary_status FROM meetings WHERE id=1").fetchone()
        assert row["summary_status"] == "pending"
