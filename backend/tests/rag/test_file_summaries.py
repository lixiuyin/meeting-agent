"""Tests for file summaries context and DB helpers."""

import os
import tempfile
from pathlib import Path

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.core.database import (  # noqa: E402
    close_all_connections,
    get_connection,
    get_write_connection,
)
from src.core.database._migrations import init_db  # noqa: E402
from src.core.database.meetings import (  # noqa: E402
    create_meeting,
    create_meeting_file,
    get_meeting_files_summaries,
    list_ready_file_ids_for_meetings,
    list_recent_ready_file_ids,
    update_meeting_file_status,
    update_meeting_file_summary,
)
from src.services.chain._formatting import _build_system_context  # noqa: E402


def _setup_db() -> None:
    """Initialize DB schema."""
    close_all_connections()
    db_path = constants_module.DATABASE_PATH
    if db_path.exists():
        db_path.unlink()
    init_db()


class TestDbHelpers:
    """Test new DB helper functions for file enumeration and summaries."""

    def test_list_ready_file_ids_for_meetings(self):
        _setup_db()
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="Meet 1", user_id="test")
            fid1 = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="a.pdf",
                file_path="/a.pdf",
                content_hash="h1",
            )
            create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="b.pdf",
                file_path="/b.pdf",
                content_hash="h2",
            )
            update_meeting_file_status(conn, fid1, "ready")
        with get_connection() as conn:
            ids = list_ready_file_ids_for_meetings(conn, [mid])
            assert ids == [fid1]

    def test_list_ready_file_ids_empty_meetings(self):
        _setup_db()
        with get_connection() as conn:
            ids = list_ready_file_ids_for_meetings(conn, [])
            assert ids == []

    def test_list_recent_ready_file_ids(self):
        _setup_db()
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="M1", user_id="test")
            fid1 = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="a.pdf",
                file_path="/a.pdf",
                content_hash="h1",
            )
            fid2 = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="b.pdf",
                file_path="/b.pdf",
                content_hash="h2",
            )
            update_meeting_file_status(conn, fid1, "ready")
            update_meeting_file_status(conn, fid2, "ready")
        with get_connection() as conn:
            ids = list_recent_ready_file_ids(conn, limit=10)
            assert len(ids) == 2

    def test_get_meeting_files_summaries(self):
        _setup_db()
        with get_write_connection() as conn:
            mid = create_meeting(conn, title="M1", user_id="test")
            fid1 = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="a.pdf",
                file_path="/a.pdf",
                content_hash="h1",
            )
            fid2 = create_meeting_file(
                conn,
                meeting_id=mid,
                file_type="pdf",
                file_name="b.pdf",
                file_path="/b.pdf",
                content_hash="h2",
            )
            update_meeting_file_summary(conn, fid1, summary="Summary of file A")
        with get_connection() as conn:
            summaries = get_meeting_files_summaries(conn, [fid1, fid2])
            assert summaries == {fid1: "Summary of file A"}

    def test_get_meeting_files_summaries_empty(self):
        _setup_db()
        with get_connection() as conn:
            summaries = get_meeting_files_summaries(conn, [])
            assert summaries == {}


class TestFileSummariesInContext:
    """Verify <file_summaries> appears in built context."""

    def test_file_summaries_tag_present(self):
        result = _build_system_context(
            memory_context="",
            session_context="",
            entity_context="",
            meeting_context="[1] some content",
            web_context="",
            file_summaries_context="[1] report.pdf: Q3 results summary",
        )
        assert "<file_summaries>" in result
        assert "</file_summaries>" in result
        assert "report.pdf" in result

    def test_no_file_summaries_when_empty(self):
        result = _build_system_context(
            memory_context="",
            session_context="",
            entity_context="",
            meeting_context="[1] some content",
            web_context="",
            file_summaries_context="",
        )
        assert "<file_summaries>" not in result
