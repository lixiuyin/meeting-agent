import json

from src.core import database as db
from src.services.continuation_preview import build_continuation_preview


def test_preflight_uses_active_scope_and_keeps_deleted_files_visible(db_conn):
    sid = db.create_session(db_conn, user_id="preview")
    session = {
        "id": sid,
        "task_state_json": json.dumps(
            {
                "active_scope": {"meeting_ids": [7], "file_ids": [123]},
                "open_questions": ["Who owns release?"],
                "recalled_memory_versions": [{"key": "task.deleted", "revision": 1}],
            }
        ),
    }
    result = build_continuation_preview(db_conn, session, "preview")
    assert result["scope"]["meeting_ids"] == [7]
    assert result["files"] == [{"file_id": 123, "file_name": None, "status": "deleted"}]
    assert result["memory_changes"][0]["status"] == "deleted"
    assert not result["saved_snapshot_available"] and not result["checkpoint_available"]
    assert result["open_questions"] == ["Who owns release?"]


def test_preflight_tolerates_legacy_and_malformed_optional_state(db_conn):
    sid = db.create_session(db_conn, user_id="preview-malformed")
    for state in (
        "[]",
        "broken",
        json.dumps({"active_scope": {"file_ids": 3}, "open_questions": {"bad": "value"}}),
    ):
        result = build_continuation_preview(
            db_conn, {"id": sid, "task_state_json": state}, "preview-malformed"
        )
        assert result["files"] == [] and result["open_questions"] == []
