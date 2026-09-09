import pytest

from src.core import database as db
from src.core.memory_query import parse_action_constraints
from src.services.memory._service._search import _entry_matches_scope


@pytest.mark.parametrize(
    "query",
    [
        "列出所有未完成的行动项",
        "列出所有未完成行动项",
        "list all tasks not completed",
        "list all incomplete tasks",
        "all tasks not yet done",
    ],
)
@pytest.mark.parametrize("historical", [False, True])
def test_unfinished_semantics_current_and_historical(db_conn, query, historical):
    for state in ("open", "in_progress", "blocked", "done", "cancelled"):
        db.set_memory(
            db_conn,
            user_id="domain",
            key=f"todo.{state}",
            value=state,
            fact_type="action_item",
            action_status=state,
        )
    rows, total = db.search_structured_memories(
        db_conn,
        user_id="domain",
        fact_types=["action_item"],
        query_text=query,
        as_of="2099-01-01" if historical else None,
    )
    assert total == 3
    assert {row["action_status"] for row in rows} == {"open", "in_progress", "blocked"}


def test_status_union_and_exclusion():
    result = parse_action_constraints("已完成或未完成的任务,排除取消的")
    assert result.matches("done") and result.matches("open")
    assert not result.matches("cancelled")


@pytest.mark.parametrize(
    "query,status",
    [("进行中的任务", "in_progress"), ("blocked tasks", "blocked"), ("open tasks", "open")],
)
def test_specific_status_is_not_expanded_to_all_unfinished_states(query, status):
    constraints = parse_action_constraints(query)
    assert constraints.included == (status,)


@pytest.mark.parametrize("historical", [False, True])
def test_scope_intersection_every_memory_path(db_conn, historical):
    for file_id in (11, 12):
        db.set_memory(
            db_conn,
            user_id="domain",
            key=f"todo.{file_id}",
            value=str(file_id),
            fact_type="action_item",
            meeting_ids=[1],
            file_ids=[file_id],
        )
    kwargs = {"user_id": "domain", "meeting_ids": [1], "file_ids": [11]}
    rows, total = db.search_structured_memories(
        db_conn, fact_types=["action_item"], **kwargs, as_of="2099-01-01" if historical else None
    )
    assert total == 1 and rows[0]["key"] == "todo.11"
    assert db.list_memory_keys_for_scope(db_conn, **kwargs) == ["todo.11"]
    assert not _entry_matches_scope([1], [12], [1], [11])


def test_mixed_fact_types_do_not_lose_decisions(db_conn):
    db.set_memory(db_conn, user_id="domain", key="decision.a", value="ship", fact_type="decision")
    rows, _ = db.search_structured_memories(
        db_conn,
        user_id="domain",
        fact_types=["action_item", "decision"],
        query_text="未完成的任务和决策",
    )
    assert rows[0]["key"] == "decision.a"
