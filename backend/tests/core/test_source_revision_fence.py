"""Commit-time source revision fencing for file-derived jobs."""

import sqlite3

import pytest

from src.core import database as db
from src.core.source_revision_fence import (
    SourceRevisionChangedError,
    activate_source_revision_fence,
    assert_active_source_revision_fence,
    meeting_file_source_token,
    meeting_file_source_tokens,
)


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE meeting_files (id INTEGER PRIMARY KEY, user_id TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO meeting_files(id,user_id,updated_at) VALUES(7,'u1','2026-09-05 10:00:00')"
    )
    return conn


def test_source_fence_accepts_unchanged_file() -> None:
    conn = _connection()
    with activate_source_revision_fence("u1", [(7, "2026-09-05 10:00:00")]):
        assert_active_source_revision_fence(conn)


@pytest.mark.parametrize("mutation", ["delete", "replace"])
def test_source_fence_rejects_deleted_or_replaced_file(mutation: str) -> None:
    conn = _connection()
    with activate_source_revision_fence("u1", [(7, "2026-09-05 10:00:00")]):
        if mutation == "delete":
            conn.execute("DELETE FROM meeting_files WHERE id=7")
        else:
            conn.execute("UPDATE meeting_files SET updated_at='2026-09-05 10:01:00' WHERE id=7")
        with pytest.raises(SourceRevisionChangedError):
            assert_active_source_revision_fence(conn)


def test_write_connection_rolls_back_stale_source_side_effect() -> None:
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Fence", user_id="source-user")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="fence.txt",
            file_path="/tmp/fence.txt",
            user_id="source-user",
        )
    with db.get_connection() as conn:
        original = db.get_meeting_file(conn, file_id, user_id="source-user")
    assert original is not None
    with db.get_write_connection() as conn:
        conn.execute(
            "UPDATE meeting_files SET updated_at='2099-01-01 00:00:00' WHERE id=?",
            (file_id,),
        )

    with activate_source_revision_fence("source-user", [(file_id, str(original["updated_at"]))]):
        with pytest.raises(SourceRevisionChangedError):
            with db.get_write_connection() as conn:
                db.set_memory(
                    conn,
                    user_id="source-user",
                    key="must.rollback",
                    value="stale fact",
                )

    with db.get_connection() as conn:
        assert db.get_memory_full(conn, user_id="source-user", key="must.rollback") is None


def test_monotonic_source_revision_rejects_same_second_semantic_edit() -> None:
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Fence", user_id="source-user")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="fence.txt",
            file_path="/tmp/fence.txt",
            user_id="source-user",
        )
        original = db.get_meeting_file(conn, file_id, user_id="source-user")
        assert original is not None
        original_token = meeting_file_source_token(original)
        db.update_meeting_file_semantics(
            conn,
            file_id,
            material_role="decision_log",
            user_id="source-user",
        )

    with activate_source_revision_fence("source-user", [(file_id, original_token)]):
        with db.get_connection() as conn, pytest.raises(SourceRevisionChangedError):
            assert_active_source_revision_fence(conn)


def test_source_revision_tokens_accept_canonical_and_legacy_aliases() -> None:
    record = {
        "source_revision": 7,
        "content_hash": "content-v7",
        "active_index_generation": "index-v7",
        "updated_at": "2026-09-08 10:00:00",
    }

    assert meeting_file_source_token(record) == "r:7"
    assert meeting_file_source_tokens(record) == {
        "r:7",
        "source:7",
        "content-v7",
        "index-v7",
        "2026-09-08 10:00:00",
    }
