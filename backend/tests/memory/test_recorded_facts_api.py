import pytest
from fastapi import HTTPException

from src.api.routers.memory import query_recorded_facts
from src.core import database as db
from src.models.schemas.fact_query import FactQueryRequest


@pytest.mark.asyncio
async def test_cross_meeting_changes_preserve_both_versions_and_evidence():
    from src.api.routers.memory import compare_recorded_facts
    from src.models.schemas.fact_query import FactChangesRequest

    with db.get_write_connection() as conn:
        for value, status, start, meeting in (
            ("Alice to ship", "open", "2029-01-01T00:00:00Z", 71),
            ("Bob shipped", "done", "2030-01-01T00:00:00Z", 72),
        ):
            db.set_memory(
                conn,
                user_id="changes-api",
                key="task.release",
                value=value,
                fact_type="action_item",
                action_status=status,
                project_id="release",
                valid_from=start,
                meeting_ids=[71, 72],
                evidence_excerpt=value,
                evidence_refs=[{"meeting_id": meeting, "file_id": meeting}],
            )
    result = await compare_recorded_facts(
        FactChangesRequest(
            before="2029-06-01T00:00:00Z",
            after="2030-06-01T00:00:00Z",
            project_id="release",
        ),
        {"user_id": "changes-api"},
    )
    assert result.total == 1
    change = result.items[0]
    assert change.kind == "changed"
    assert change.before.action_status == "open" and change.after.action_status == "done"
    assert change.before.evidence_refs[0]["meeting_id"] == 71
    assert change.after.evidence_refs[0]["meeting_id"] == 72
    assert {"action_status", "value", "evidence_refs"} <= set(change.changed_fields)
    assert not result.extraction_complete


@pytest.mark.asyncio
async def test_pagination_fences_reordering_even_without_revision_update():
    principal = {"user_id": "order-api"}
    with db.get_write_connection() as conn:
        for i in range(3):
            db.set_memory(
                conn, user_id=principal["user_id"], key=f"t.{i}", value=str(i), fact_type="decision"
            )
    request = FactQueryRequest(limit=1)
    first = await query_recorded_facts(request, principal)
    with db.get_write_connection() as conn:
        conn.execute(
            "UPDATE user_memories SET salience=salience+1 WHERE user_id=? AND key='t.0'",
            (principal["user_id"],),
        )
    with pytest.raises(HTTPException) as error:
        await query_recorded_facts(
            request.model_copy(update={"offset": 1, "snapshot": first.snapshot}), principal
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_paginated_facts_are_counted_scoped_and_revision_fenced():
    with db.get_write_connection() as conn:
        for i in range(4):
            db.set_memory(
                conn,
                user_id="facts-api",
                key=f"task.{i}",
                value=str(i),
                fact_type="action_item",
                action_status="open",
            )
        db.set_memory(
            conn, user_id="other", key="task.private", value="private", fact_type="action_item"
        )
    principal = {"user_id": "facts-api"}
    request = FactQueryRequest(query="未完成的任务", limit=2)
    first = await query_recorded_facts(request, principal)
    assert first.total == 4 and first.returned == 2 and first.next_offset == 2
    assert not first.recorded_set_complete and not first.extraction_complete
    second = await query_recorded_facts(
        request.model_copy(update={"offset": 2, "snapshot": first.snapshot}), principal
    )
    assert second.next_offset is None
    assert not ({item.key for item in first.items} & {item.key for item in second.items})
    with db.get_write_connection() as conn:
        db.set_memory(
            conn, user_id="facts-api", key="task.0", value="changed", fact_type="action_item"
        )
    with pytest.raises(HTTPException) as error:
        await query_recorded_facts(
            request.model_copy(update={"offset": 2, "snapshot": first.snapshot}), principal
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_review_queue_is_principal_project_scoped_and_includes_competing_evidence():
    from src.api.routers.memory import ReviewQueryRequest, review_candidates

    with db.get_write_connection() as conn:
        for user in ("review-a", "review-b"):
            db.set_memory(
                conn,
                user_id=user,
                key="old",
                value="Alice owns QA",
                fact_type="action_item",
                project_id="release",
                evidence_excerpt="Original owner Alice",
            )
            db.set_memory(
                conn,
                user_id=user,
                key="candidate",
                value="Bob owns QA",
                fact_type="action_item",
                project_id="release",
                assertion_status="disputed",
                conflicts_with=["old"],
                evidence_excerpt="Proposed owner Bob",
            )
    result = await review_candidates(
        ReviewQueryRequest(project_id="release"), {"user_id": "review-a"}
    )
    assert result["total"] == 1 and result["items"][0]["key"] == "candidate"
    assert result["conflicts"]["candidate"][0]["evidence_excerpt"] == "Original owner Alice"
    empty = await review_candidates(ReviewQueryRequest(project_id="other"), {"user_id": "review-a"})
    assert empty["total"] == 0


@pytest.mark.asyncio
async def test_review_queue_excludes_legacy_reference_project_facts_and_fences_pages():
    from src.api.routers.memory import ReviewQueryRequest, review_candidates

    principal = {"user_id": "review-snapshot"}
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=principal["user_id"],
            key="course.stat8307.assessment",
            value="Reference course assessment",
            source="auto_extracted",
            fact_type="project_fact",
            assertion_status="confirmed",
        )
        for index in range(3):
            db.set_memory(
                conn,
                user_id=principal["user_id"],
                key=f"task.review.{index}",
                value=f"Task {index}",
                source="auto_extracted",
                fact_type="action_item",
                assertion_status="confirmed",
            )

    first = await review_candidates(ReviewQueryRequest(limit=1), principal)
    assert first["total"] == 3
    assert first["items"][0]["key"] != "course.stat8307.assessment"
    with db.get_write_connection() as conn:
        db.update_memory(
            conn,
            user_id=principal["user_id"],
            key=first["items"][0]["key"],
            assertion_status="retracted",
            expected_revision=first["items"][0]["revision"],
            fields={"assertion_status"},
        )
    with pytest.raises(HTTPException) as error:
        await review_candidates(
            ReviewQueryRequest(limit=1, offset=1, snapshot=first["snapshot"]), principal
        )
    assert error.value.status_code == 409
