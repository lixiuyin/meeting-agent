from src.core import database as db
from src.core.project_resolution import resolve_project_ids


def test_multi_project_mentions_do_not_expand_to_all_projects(db_conn):
    for project in ("atlas", "atlas_release", "orbit", "private"):
        db.set_memory(
            db_conn,
            user_id="projects",
            key=f"task.{project}",
            value=project,
            fact_type="action_item",
            project_id=project,
        )
    assert resolve_project_ids(db_conn, "projects", "atlas release tasks") == ("atlas_release",)
    assert resolve_project_ids(db_conn, "projects", "atlas2 tasks") == ()
    rows, total = db.search_structured_memories(
        db_conn,
        user_id="projects",
        fact_types=["action_item"],
        query_text="compare atlas and orbit",
    )
    assert total == 2 and {row["project_id"] for row in rows} == {"atlas", "orbit"}
    assert resolve_project_ids(db_conn, "another-user", "atlas release") == ()


def test_project_aliases_are_user_scoped_and_do_not_import_person_aliases(db_conn):
    db.set_memory(db_conn, user_id="alias", key="task.release", value="ship", project_id="atlas")
    db.upsert_entity(
        db_conn, user_id="alias", name="atlas", entity_type="project", aliases=["阿特拉斯"]
    )
    assert resolve_project_ids(db_conn, "alias", "阿特拉斯的待办") == ("atlas",)
    assert resolve_project_ids(db_conn, "other", "阿特拉斯的待办") == ()


def test_extraction_resolves_only_explicit_unambiguous_owned_project_mentions(db_conn):
    from src.core.database.projects import save_project
    from src.core.project_resolution import resolve_assertion_project

    save_project(db_conn, "u", "project-123", "Atlas", ["阿特拉斯"], [])
    assert resolve_assertion_project(db_conn, "u", None, "Alice owns Atlas.") == (
        "project-123",
        "Atlas",
    )
    assert resolve_assertion_project(db_conn, "u", "atlas", "Alice owns Atlas.") == (
        "project-123",
        "Atlas",
    )
    assert resolve_assertion_project(db_conn, "other", None, "Alice owns Atlas.") == (None, None)
    assert resolve_assertion_project(db_conn, "u", None, "Alice owns the report.") == (None, None)
    save_project(db_conn, "u", "project-456", "Orbit", ["Atlas"], [])
    assert resolve_assertion_project(db_conn, "u", None, "Alice owns Atlas.") == (None, None)
