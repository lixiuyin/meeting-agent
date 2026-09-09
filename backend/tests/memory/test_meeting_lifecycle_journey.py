import json
from contextlib import contextmanager

import pytest

from src.core import database as db
from src.core.database.memory_lifecycle import archive_memories, valid_profile
from src.core.memory_policy import is_active_memory


def test_capacity_retires_vectors_but_preserves_complete_business_set(db_conn, monkeypatch):
    from src.core.config import settings
    from src.services.memory._service._crud import _MemoryCrudMixin

    for i in range(12):
        db.set_memory(
            db_conn,
            user_id="journey",
            key=f"task.{i}",
            value="prepare report",
            fact_type="action_item",
            action_status="open",
            valid_from="2026-01-01",
        )

    @contextmanager
    def connection():
        yield db_conn

    monkeypatch.setattr("src.services.memory._service._crud.get_write_connection", connection)
    monkeypatch.setattr(settings, "MEMORY_MAX_PER_USER", 10)
    _MemoryCrudMixin()._enforce_memory_cap("journey")
    rows = db.list_memories(db_conn, user_id="journey")
    assert sum(is_active_memory(row) for row in rows) == 10
    assert len(rows) == 12
    historical, total = db.search_structured_memories(
        db_conn,
        user_id="journey",
        fact_types=["action_item"],
        as_of="2026-02-01",
    )
    assert total == len(historical) == 12
    current, total = db.search_structured_memories(
        db_conn,
        user_id="journey",
        fact_types=["action_item"],
    )
    assert total == len(current) == 12


def test_assignment_change_and_expiry_preserve_old_meeting_state(db_conn):
    db.set_memory(
        db_conn,
        user_id="journey",
        key="task.report",
        value="prepare report",
        fact_type="action_item",
        assignee="Alice",
        action_status="open",
        valid_from="2026-01-01",
    )
    assert db.update_memory(
        db_conn,
        user_id="journey",
        key="task.report",
        expected_revision=1,
        assignee="Bob",
        action_status="done",
        valid_from="2026-02-01",
        fields={"assignee", "action_status", "valid_from"},
    )
    before, _ = db.search_structured_memories(
        db_conn, user_id="journey", fact_types=["action_item"], as_of="2026-01-15", assignee="Alice"
    )
    assert len(before) == 1 and before[0]["action_status"] == "open"
    db_conn.execute("UPDATE user_memories SET expires_at='2026-02-02' WHERE user_id='journey'")
    assert db.delete_expired_memories(db_conn) == 1
    after, _ = db.search_structured_memories(
        db_conn, user_id="journey", fact_types=["action_item"], as_of="2026-02-01", assignee="Bob"
    )
    assert len(after) == 1 and after[0]["action_status"] == "done"
    assert len(db.list_memory_versions(db_conn, user_id="journey", key="task.report")) == 2


@pytest.mark.parametrize("change", ["edit", "archive", "delete", "expiry"])
def test_profile_loses_authority_when_source_changes(db_conn, change):
    db.set_memory(db_conn, user_id="profile-test", key="preference.language", value="Chinese")
    db.set_memory(
        db_conn, user_id="profile-test", key="__profile__", value="Uses Chinese", source="profile"
    )
    assert valid_profile(db_conn, "profile-test") is None  # Legacy profiles are unverified.
    db_conn.execute(
        "INSERT INTO memory_profile_provenance(user_id,profile_revision,source_revisions) VALUES(?,?,?)",
        ("profile-test", 1, json.dumps({"preference.language": 1})),
    )
    assert valid_profile(db_conn, "profile-test") == "Uses Chinese"
    if change == "edit":
        db.update_memory(
            db_conn,
            user_id="profile-test",
            key="preference.language",
            expected_revision=1,
            value="English",
        )
    elif change == "archive":
        row = db.get_memory_full(db_conn, user_id="profile-test", key="preference.language")
        archive_memories(db_conn, [row], reason="test")
    elif change == "delete":
        db.delete_memory(db_conn, user_id="profile-test", key="preference.language")
    else:
        db_conn.execute(
            "UPDATE user_memories SET expires_at='2020-01-01' WHERE key='preference.language'"
        )
    assert valid_profile(db_conn, "profile-test") is None


def test_archive_rolls_back_if_cleanup_outbox_cannot_be_written(db_conn):
    db.set_memory(db_conn, user_id="journey", key="task", value="keep", embedding_id="vector")
    row = db.get_memory_full(db_conn, user_id="journey", key="task")
    db_conn.execute(
        "CREATE TRIGGER reject_outbox BEFORE INSERT ON pending_vector_deletions BEGIN SELECT RAISE(ABORT,'no'); END"
    )
    with pytest.raises(Exception, match="no"):
        archive_memories(db_conn, [row], reason="capacity")
    assert db.get_memory_full(db_conn, user_id="journey", key="task")["archived_at"] is None


def test_same_text_upsert_closes_previous_assignment(db_conn):
    for assignee, status, day in [("Alice", "open", "2026-01-01"), ("Bob", "done", "2026-02-01")]:
        db.set_memory(
            db_conn,
            user_id="journey",
            key="same-task",
            value="prepare report",
            fact_type="action_item",
            assignee=assignee,
            action_status=status,
            valid_from=day,
        )
    old, total = db.search_structured_memories(
        db_conn, user_id="journey", fact_types=["action_item"], as_of="2026-01-15", assignee="Alice"
    )
    assert total == 1 and old[0]["action_status"] == "open"
    after, total = db.search_structured_memories(
        db_conn, user_id="journey", fact_types=["action_item"], as_of="2026-02-15", assignee="Alice"
    )
    assert total == 0 and after == []


def test_review_cannot_supersede_a_newly_edited_competitor(db_conn):
    from src.core.database.memories import MemoryRevisionConflictError, resolve_memory_conflict

    db.set_memory(db_conn, user_id="journey", key="old", value="Alice")
    db.set_memory(
        db_conn,
        user_id="journey",
        key="candidate",
        value="Bob",
        assertion_status="disputed",
        conflicts_with=["old"],
    )
    db.update_memory(db_conn, user_id="journey", key="old", value="Carol", expected_revision=1)
    with pytest.raises(MemoryRevisionConflictError, match="changed after review"):
        resolve_memory_conflict(
            db_conn,
            user_id="journey",
            winner_key="candidate",
            expected_revision=1,
            conflicting_keys=["old"],
            expected_conflict_revisions={"old": 1},
        )
    assert db.get_memory_full(db_conn, user_id="journey", key="old")["value"] == "Carol"
    assert (
        db.get_memory_full(db_conn, user_id="journey", key="candidate")["assertion_status"]
        == "disputed"
    )
