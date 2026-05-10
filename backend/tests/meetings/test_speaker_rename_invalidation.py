"""Tests for meeting summary invalidation after speaker rename / file delete."""

import pytest


@pytest.fixture
def db_conn(tmp_path):
    """Provide an in-memory SQLite connection with full schema."""
    import sqlite3

    from src.core.database._migrations import _MIGRATIONS, _apply_migration

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


def _seed_meeting_with_files(conn, meeting_id=1):
    conn.execute(
        "INSERT OR IGNORE INTO meetings (id, title, status) VALUES (?, 'Test', 'ready')",
        (meeting_id,),
    )
    for fid in (1, 2):
        conn.execute(
            "INSERT OR IGNORE INTO meeting_files "
            "(id, meeting_id, file_type, file_name, file_path, status, transcript, summary) "
            "VALUES (?, ?, 'video', 'test.mp4', '/tmp/test.mp4', 'ready', 'hello world', 'a summary')",
            (fid, meeting_id),
        )
    conn.execute(
        "INSERT OR IGNORE INTO meeting_summaries (meeting_id, summary, contributing_file_ids) "
        "VALUES (?, 'meeting summary', '[1,2]')",
        (meeting_id,),
    )
    conn.commit()


class TestSpeakerRenameInvalidation:
    @pytest.mark.unit
    def test_invalidate_clears_meeting_summary(self, db_conn):
        _seed_meeting_with_files(db_conn)

        from src.core.database.meetings import clear_meeting_summary

        clear_meeting_summary(db_conn, 1)

        row = db_conn.execute("SELECT * FROM meeting_summaries WHERE meeting_id=1").fetchone()
        assert row is None

    @pytest.mark.unit
    def test_meeting_summary_status_reset_on_invalidate(self, db_conn):
        _seed_meeting_with_files(db_conn)
        db_conn.execute("UPDATE meetings SET summary_status='ready' WHERE id=1")

        from src.core.database.meetings import clear_meeting_summary

        clear_meeting_summary(db_conn, 1)

        row = db_conn.execute("SELECT summary_status FROM meetings WHERE id=1").fetchone()
        assert row["summary_status"] == "pending"

    @pytest.mark.unit
    def test_file_summary_preserved_after_meeting_invalidate(self, db_conn):
        _seed_meeting_with_files(db_conn)

        from src.core.database.meetings import clear_meeting_summary

        clear_meeting_summary(db_conn, 1)

        rows = db_conn.execute("SELECT summary FROM meeting_files WHERE meeting_id=1").fetchall()
        assert all(r["summary"] is not None for r in rows)


class TestFileDeleteInvalidation:
    @pytest.mark.unit
    def test_file_delete_clears_file_summary(self, db_conn):
        _seed_meeting_with_files(db_conn)

        from src.core.database.meetings import clear_file_summary

        clear_file_summary(db_conn, 1)

        row = db_conn.execute("SELECT summary FROM meeting_files WHERE id=1").fetchone()
        assert row["summary"] is None

    @pytest.mark.unit
    def test_file_delete_preserves_other_file_summaries(self, db_conn):
        _seed_meeting_with_files(db_conn)

        from src.core.database.meetings import clear_file_summary

        clear_file_summary(db_conn, 1)

        row = db_conn.execute("SELECT summary FROM meeting_files WHERE id=2").fetchone()
        assert row["summary"] is not None
