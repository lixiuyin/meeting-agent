from src.core import database as db
from src.core.memory_policy import is_active_memory


def test_memory_versions_capture_updates_and_retraction() -> None:
    user_id = "fact-lifecycle-user"
    key = "decision.release.date"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key=key,
            value="Release on October 1",
            fact_type="decision",
            project_id="release",
        )
        db.set_memory(
            conn,
            user_id=user_id,
            key=key,
            value="Release on October 8",
            fact_type="decision",
            project_id="release",
        )
        assert db.update_memory(
            conn,
            user_id=user_id,
            key=key,
            expected_revision=2,
            assertion_status="retracted",
        )

    with db.get_connection() as conn:
        current = db.get_memory_full(conn, user_id=user_id, key=key)
        versions = db.list_memory_versions(conn, user_id=user_id, key=key)

    assert current is not None
    assert current["assertion_status"] == "retracted"
    assert current["retracted_at"] is not None
    assert not is_active_memory(current)
    assert [row["revision"] for row in versions] == [3, 2, 1]
    assert [row["value"] for row in versions] == [
        "Release on October 8",
        "Release on October 8",
        "Release on October 1",
    ]


def test_structured_memory_scope_reports_complete_total() -> None:
    user_id = "structured-scope-user"
    with db.get_write_connection() as conn:
        for index in range(3):
            db.set_memory(
                conn,
                user_id=user_id,
                key=f"todo.alpha.item_{index}",
                value=f"Action {index}",
                fact_type="action_item",
                project_id="alpha",
                meeting_ids=[11],
            )
        db.set_memory(
            conn,
            user_id=user_id,
            key="todo.beta.item",
            value="Out of scope",
            fact_type="action_item",
            project_id="beta",
            meeting_ids=[12],
        )

    with db.get_connection() as conn:
        rows, total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["action_item"],
            meeting_ids=[11],
            limit=2,
        )

    assert len(rows) == 2
    assert total == 3
    assert {row["project_id"] for row in rows} == {"alpha"}


def test_structured_memory_infers_project_before_applying_limit() -> None:
    user_id = "structured-project-user"
    with db.get_write_connection() as conn:
        for project in ("atlas_release", "orbit_release"):
            for index in range(3):
                db.set_memory(
                    conn,
                    user_id=user_id,
                    key=f"todo.{project}.item_{index}",
                    value=f"{project} action {index}",
                    fact_type="action_item",
                    project_id=project,
                )

    with db.get_connection() as conn:
        rows, total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["action_item"],
            query_text="List all Atlas Release tasks",
            limit=2,
        )

    assert len(rows) == 2
    assert total == 3
    assert {row["project_id"] for row in rows} == {"atlas_release"}


def test_structured_memory_applies_fact_validity_as_of() -> None:
    user_id = "structured-as-of-user"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key="project.atlas.status",
            value="active",
            fact_type="project_fact",
            project_id="atlas",
            valid_from="2030-01-01T00:00:00+00:00",
        )

    with db.get_connection() as conn:
        rows, total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["project_fact"],
            as_of="2029-12-31T00:00:00+00:00",
        )

    assert rows == []
    assert total == 0


def test_structured_memory_reconstructs_replaced_value_as_of() -> None:
    user_id = "structured-history-user"
    key = "project.atlas.owner"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key=key,
            value="Alice",
            fact_type="project_fact",
            project_id="atlas",
            valid_from="2029-01-01T00:00:00+00:00",
        )
        db.set_memory(
            conn,
            user_id=user_id,
            key=key,
            value="Bob",
            fact_type="project_fact",
            project_id="atlas",
            valid_from="2030-01-01T00:00:00+00:00",
        )

    with db.get_connection() as conn:
        old_rows, old_total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["project_fact"],
            project_id="atlas",
            as_of="2029-06-01T00:00:00+00:00",
        )
        new_rows, new_total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["project_fact"],
            project_id="atlas",
            as_of="2030-06-01T00:00:00+00:00",
        )

    assert old_total == new_total == 1
    assert old_rows[0]["value"] == "Alice"
    assert new_rows[0]["value"] == "Bob"


def test_structured_memory_supports_business_and_system_time_snapshots() -> None:
    user_id = "bitemporal-history-user"
    key = "project.orbit.owner"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key=key,
            value="Alice",
            fact_type="project_fact",
            valid_from="2025-01-01T00:00:00+00:00",
        )
        db.set_memory(
            conn,
            user_id=user_id,
            key=key,
            value="Bob",
            fact_type="project_fact",
            valid_from="2025-03-01T00:00:00+00:00",
        )
        conn.execute(
            "UPDATE memory_fact_versions SET recorded_at=?, recorded_to=? "
            "WHERE user_id=? AND memory_key=? AND revision=1",
            ("2025-01-10T00:00:00+00:00", "2025-03-10T00:00:00+00:00", user_id, key),
        )
        conn.execute(
            "UPDATE memory_fact_versions SET recorded_at=? "
            "WHERE user_id=? AND memory_key=? AND revision=2",
            ("2025-03-10T00:00:00+00:00", user_id, key),
        )

    def _snapshot(valid_at: str, known_at: str) -> str:
        with db.get_connection() as conn:
            rows, total = db.search_structured_memories(
                conn,
                user_id=user_id,
                fact_types=["project_fact"],
                as_of=valid_at,
                known_at=known_at,
            )
        assert total == 1
        return str(rows[0]["value"])

    assert _snapshot("2025-04-01T00:00:00+00:00", "2025-02-01T00:00:00+00:00") == "Alice"
    assert _snapshot("2025-04-01T00:00:00+00:00", "2025-04-01T00:00:00+00:00") == "Bob"
    assert _snapshot("2025-02-01T00:00:00+00:00", "2025-04-01T00:00:00+00:00") == "Alice"


def test_historical_search_does_not_resurrect_old_fact_type() -> None:
    user_id = "structured-type-change-user"
    key = "project.atlas.status"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key=key,
            value="approved",
            fact_type="decision",
            valid_from="2025-01-01T00:00:00+00:00",
        )
        assert db.update_memory(
            conn,
            user_id=user_id,
            key=key,
            expected_revision=1,
            fact_type="project_fact",
        )

    with db.get_connection() as conn:
        old_type, old_total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["decision"],
            as_of="2026-01-01T00:00:00+00:00",
        )
        new_type, new_total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["project_fact"],
            as_of="2026-01-01T00:00:00+00:00",
        )
    assert (old_type, old_total) == ([], 0)
    assert new_total == 1
    assert new_type[0]["revision"] == 2


def test_historical_search_stops_recalling_superseded_fact_after_transition() -> None:
    user_id = "structured-superseded-history-user"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key="project.atlas.owner.legacy",
            value="Alice",
            fact_type="project_fact",
            project_id="atlas",
            valid_from="2020-01-01T00:00:00+00:00",
        )
        db.mark_memory_superseded(
            conn,
            user_id=user_id,
            key="project.atlas.owner.legacy",
            superseded_by="project.atlas.owner.current",
        )

    with db.get_connection() as conn:
        before, before_total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["project_fact"],
            as_of="2025-01-01T00:00:00+00:00",
        )
        after, after_total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["project_fact"],
            as_of="2100-01-01T00:00:00+00:00",
        )

    assert before_total == 1
    assert before[0]["value"] == "Alice"
    assert after_total == 0
    assert after == []


def test_structured_memory_filters_overdue_open_actions() -> None:
    user_id = "structured-overdue-user"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key="todo.atlas.open",
            value="Open overdue task",
            fact_type="action_item",
            project_id="atlas",
            action_status="open",
            due_at="2029-01-01T00:00:00+00:00",
        )
        db.set_memory(
            conn,
            user_id=user_id,
            key="todo.atlas.done",
            value="Completed task",
            fact_type="action_item",
            project_id="atlas",
            action_status="done",
            due_at="2029-01-01T00:00:00+00:00",
        )

    with db.get_connection() as conn:
        rows, total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["action_item"],
            query_text="Show overdue Atlas tasks",
            as_of="2030-01-01T00:00:00+00:00",
        )

    assert total == 1
    assert [row["key"] for row in rows] == ["todo.atlas.open"]


def test_historical_action_filter_does_not_fall_back_to_an_old_matching_revision() -> None:
    user_id = "structured-action-version-user"
    key = "todo.atlas.ship"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key=key,
            value="Ship Atlas",
            fact_type="action_item",
            project_id="atlas",
            action_status="open",
            valid_from="2025-01-01T00:00:00+00:00",
        )
        assert db.update_memory(
            conn,
            user_id=user_id,
            key=key,
            expected_revision=1,
            action_status="done",
        )

    with db.get_connection() as conn:
        rows, total = db.search_structured_memories(
            conn,
            user_id=user_id,
            fact_types=["action_item"],
            query_text="Show open Atlas tasks",
            as_of="2100-01-01T00:00:00+00:00",
        )

    assert rows == []
    assert total == 0
