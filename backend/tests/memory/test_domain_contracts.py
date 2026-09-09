import pytest

from src.api.routers.memory import _facts_snapshot
from src.core import database as db
from src.core.database.projects import project_file_ids, save_project
from src.core.material_role import infer_material_role
from src.core.memory_admission import file_memory_policy, is_reference_memory, reference_memory_sql
from src.core.memory_query import parse_action_constraints
from src.core.project_resolution import resolve_project_ids
from src.models.schemas.chat import SourceResponse
from src.models.schemas.fact_query import FactQueryRequest
from src.models.schemas.sessions import SessionSourceResponse


@pytest.mark.parametrize(
    "name,kind", [("lecture.mp4", "video"), ("talk.mp3", "audio"), ("课程录音.mp3", "audio")]
)
def test_media_format_does_not_authorize_project_extraction(name, kind):
    assert infer_material_role(name, kind) == "attachment"
    assert file_memory_policy({"file_name": name, "file_type": kind}) == "knowledge_only"
    assert (
        file_memory_policy({"business_domain": "course", "material_role": "transcript"})
        == "knowledge_only"
    )
    assert (
        file_memory_policy({"business_domain": "meeting", "material_role": "transcript"})
        == "project_state"
    )


def test_historical_source_preserves_all_live_fields():
    assert set(SourceResponse.model_fields) <= set(SessionSourceResponse.model_fields)
    source = SourceResponse(
        meeting_id=1,
        meeting_title="m",
        content="c",
        score=0.2,
        source_kind="image",
        content_type="image_ocr",
        image_path="asset.png",
        slide_number=4,
        document_revision="v2",
        heading_path=["intro"],
    )
    assert (
        SessionSourceResponse.model_validate(source.model_dump()).model_dump()
        == source.model_dump()
    )


def test_project_binding_checks_ownership_before_replacing(db_conn):
    meeting = db.create_meeting(db_conn, title="mine", user_id="a")
    other = db.create_meeting(db_conn, title="private", user_id="b")

    def file(mid):
        return db.create_meeting_file(
            db_conn, meeting_id=mid, file_type="pdf", file_name="a.pdf", file_path="a.pdf"
        )

    mine, private = file(meeting), file(other)
    save_project(db_conn, "a", "atlas", "Atlas release", ["发布"], [mine])
    assert resolve_project_ids(db_conn, "a", "发布计划") == ("atlas",)
    assert resolve_project_ids(db_conn, "b", "发布计划") == ()
    with pytest.raises(ValueError):
        save_project(db_conn, "a", "atlas", "bad", [], [private])
    assert project_file_ids(db_conn, "a", ("atlas",)) == [mine]
    assert project_file_ids(db_conn, "b", ("atlas",)) == []


def test_candidate_admission_precedes_vector_top_k(db_conn):
    for n in range(30):
        db.set_memory(
            db_conn, user_id="a", key=f"topic.item{n}", value="reference", source="auto_extracted"
        )
    db.set_memory(
        db_conn,
        user_id="a",
        key="task.mine",
        value="work",
        fact_type="action_item",
        project_id="atlas",
        action_status="open",
    )
    db.set_memory(
        db_conn,
        user_id="a",
        key="task.other",
        value="work",
        fact_type="action_item",
        project_id="orbit",
        action_status="open",
    )
    keys = db.list_memory_keys_for_scope(
        db_conn,
        user_id="a",
        exclude_reference=True,
        project_ids=("atlas",),
        action_constraints=parse_action_constraints("unfinished"),
    )
    assert keys == ["task.mine"]
    rows, total = db.list_and_count_memories(db_conn, user_id="a", memory_kind="reference")
    assert total == 30 and len(rows) == 30


def test_reference_sql_matches_python_policy(db_conn):
    for index, fields in enumerate(
        [
            {},
            {"source": "manual"},
            {"category": "explicit_memory"},
            {"project_id": "atlas"},
            {"assignee": "Alice"},
            {"predicate": "owner"},
            {"fact_type": "decision"},
        ]
    ):
        db.set_memory(
            db_conn,
            user_id="a",
            key=f"topic.item{index}",
            value="v",
            source="auto_extracted",
            **{k: v for k, v in fields.items() if k != "source"},
        )
        if "source" in fields:
            db_conn.execute(
                "UPDATE user_memories SET source=? WHERE key=?",
                (fields["source"], f"topic.item{index}"),
            )
    for row in db_conn.execute(
        "SELECT m.*, " + reference_memory_sql() + " AS reference FROM user_memories m"
    ):
        assert bool(row["reference"]) == is_reference_memory(dict(row))


def test_pagination_epoch_changes_on_mutation_not_reads(db_conn):
    body = FactQueryRequest()
    before = _facts_snapshot(db_conn, "a", body)
    db.set_memory(db_conn, user_id="a", key="task.one", value="v", fact_type="action_item")
    after = _facts_snapshot(db_conn, "a", body)
    assert before != after
    assert _facts_snapshot(db_conn, "a", body) == after
    db.set_memory(db_conn, user_id="b", key="task.one", value="v")
    assert _facts_snapshot(db_conn, "a", body) == after


def test_history_selects_latest_version_before_status_filter(db_conn):
    db.set_memory(
        db_conn,
        user_id="a",
        key="task.one",
        value="v",
        fact_type="action_item",
        action_status="open",
        valid_from="2026-01-01",
    )
    db.set_memory(
        db_conn,
        user_id="a",
        key="task.one",
        value="v",
        fact_type="action_item",
        action_status="done",
        valid_from="2026-02-01",
    )
    rows, total = db.search_structured_memories(
        db_conn,
        user_id="a",
        fact_types=["action_item"],
        query_text="unfinished",
        as_of="2026-03-01",
    )
    assert rows == [] and total == 0
    rows, total = db.search_structured_memories(
        db_conn, user_id="a", fact_types=["action_item"], as_of="2026-03-01", limit=1
    )
    assert total == 1 and rows[0]["action_status"] == "done"


@pytest.mark.asyncio
async def test_unbound_project_scope_and_memory_filter_survive_continuation(db_conn, monkeypatch):
    import contextlib
    import json
    import sqlite3

    from src.services.chain._context import PipelineContext
    from src.services.chain._steps_retrieve import prepare_query_plan
    from src.services.chain._steps_session import _restore_saved_snapshot

    path = db_conn.execute("PRAGMA database_list").fetchone()[2]

    @contextlib.contextmanager
    def connection():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(db, "get_connection", connection)
    monkeypatch.setattr("src.services.chain._steps_retrieve.get_connection", connection)
    save_project(db_conn, "a", "atlas", "Atlas", [], [])
    db_conn.commit()
    ctx = PipelineContext(question="Atlas unfinished tasks", user_id="a")
    await prepare_query_plan(ctx)
    assert ctx.file_ids == [-1]
    assert ctx.memory_scope_override == ()
    assert ctx.query_plan.project_ids == ("atlas",)

    continued = PipelineContext(question="continue", user_id="a", continuation_mode="saved_scope")
    _restore_saved_snapshot(
        continued,
        {
            "task_state_json": json.dumps(
                {
                    "schema_version": 4,
                    "active_scope": {
                        "file_ids": [],
                        "empty_file_scope": True,
                        "project_ids": ["atlas"],
                        "memory_scope_file_ids": [],
                    },
                }
            )
        },
        None,
    )
    await prepare_query_plan(continued)
    assert continued.file_ids == [-1]
    assert continued.memory_scope_override == ()
    assert continued.query_plan.project_ids == ("atlas",)


@pytest.mark.asyncio
async def test_query_rewrite_cannot_introduce_an_unrequested_project_scope(db_conn, monkeypatch):
    import contextlib
    import sqlite3

    from src.services.chain._context import PipelineContext
    from src.services.chain._steps_retrieve import prepare_query_plan

    path = db_conn.execute("PRAGMA database_list").fetchone()[2]

    @contextlib.contextmanager
    def connection():
        with contextlib.closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            yield conn

    monkeypatch.setattr(db, "get_connection", connection)
    monkeypatch.setattr("src.services.chain._steps_retrieve.get_connection", connection)
    save_project(db_conn, "rewrite_scope", "atlas", "Atlas", [], [])
    db_conn.commit()
    ctx = PipelineContext(
        question="Compare the model training stages",
        rewritten_query="Compare the Atlas model training stages",
        user_id="rewrite_scope",
    )
    await prepare_query_plan(ctx)
    assert ctx.file_ids is None
    assert ctx.query_plan.project_ids == ()
