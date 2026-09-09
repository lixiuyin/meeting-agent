"""Different model encodings of the same source must preserve business identity."""

import json
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from src.core import database as db
from src.services.memory import MemoryService
from src.services.memory._extractor import extract_facts


def parse(quote, **fields):
    facts = extract_facts(
        content=json.dumps([{"importance": 4, "evidence_quote": quote, **fields}]),
        question=quote,
        answer="",
        evidence_text="",
        max_facts=5,
    )
    assert len(facts) == 1
    return asdict(facts[0])


@pytest.fixture
def service(monkeypatch):
    result = MemoryService()
    monkeypatch.setattr(result, "search_semantic", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "src.services.memory._service._index_sync.index_current_memory", lambda *_: True
    )
    monkeypatch.setattr(result, "_enforce_memory_cap", lambda *_: None)
    return result


@pytest.mark.asyncio
async def test_owner_orientation_and_display_variations_do_not_create_duplicates(service):
    quote = "Maya owns Nova."
    variants = [
        {
            "key": "project.nova.maya.owns",
            "value": "Nova",
            "subject": "Maya",
            "predicate": "owns",
            "object_value": "Nova",
        },
        {
            "key": "project.nova.owner",
            "value": "Maya",
            "subject": "Nova",
            "predicate": "owner",
            "object_value": "Maya",
        },
        {
            "key": "project.nova.owner",
            "value": quote,
            "subject": "Nova",
            "predicate": "owner",
            "object_value": "Maya",
        },
    ]
    outcomes = []
    for fields in variants:
        fact = parse(quote, project_id="nova", fact_type="project_fact", **fields)
        outcomes.append(await service.store_extracted_fact("schema-user", **fact))
    assert outcomes == [True, False, False]
    with db.get_connection() as conn:
        rows = db.list_memories(conn, user_id="schema-user")
    assert len(rows) == 1
    assert rows[0]["key"] == "project.nova.owner"
    assert rows[0]["object_value"] == "Maya"
    assert rows[0]["revision"] == 1


@pytest.mark.asyncio
async def test_first_observation_keeps_explicit_review_scope(service):
    for scope, item in [("Launch Review", "rollout plan"), ("Privacy Review", "data policy")]:
        quote = f"In the {scope}, Sam owns the {item}."
        fact = parse(
            quote,
            key="person.sam.role",
            value=item,
            subject="Sam",
            predicate="owns",
            object_value=item,
        )
        assert await service.store_extracted_fact("scope-user", **fact)
    with db.get_connection() as conn:
        rows = db.list_memories(conn, user_id="scope-user")
    assert {row["project_id"] for row in rows} == {"launch_review", "privacy_review"}
    assert all(row["assertion_status"] == "confirmed" for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("predicate", ["depends_on", "dependency", "depends"])
async def test_dependency_aliases_survive_reference_admission(service, predicate):
    quote = "The service rollout depends on legal approval."
    fact = parse(
        quote,
        key="topic.rollout.dependency",
        value="legal approval",
        subject="service rollout",
        predicate=predicate,
        object_value="legal approval",
    )
    assert await service.store_extracted_fact("dependency-user", **fact)
    with db.get_connection() as conn:
        row = db.get_memory_full(conn, user_id="dependency-user", key="topic.rollout.dependency")
    assert row["predicate"] == "dependency"
    assert row["assertion_status"] == "confirmed"


@pytest.mark.parametrize("quote", ["If funded, Maya owns Nova.", "Jon said that Maya owns Nova."])
@pytest.mark.asyncio
async def test_normalizing_owner_orientation_does_not_confirm_condition_or_report(service, quote):
    fact = parse(
        quote,
        key="project.nova.maya.owns",
        value="Nova",
        project_id="nova",
        subject="Maya",
        predicate="owns",
        object_value="Nova",
    )
    assert await service.store_extracted_fact("pending-user", **fact)
    with db.get_connection() as conn:
        rows = db.list_memories(conn, user_id="pending-user")
    assert len(rows) == 1 and rows[0]["assertion_status"] == "pending"


def test_reversed_owner_is_still_rejected():
    facts = extract_facts(
        content=json.dumps(
            [
                {
                    "key": "project.nova.owner",
                    "value": "Maya",
                    "project_id": "nova",
                    "subject": "Nova",
                    "predicate": "owner",
                    "object_value": "Jon",
                    "evidence_quote": "Maya owns Nova.",
                }
            ]
        ),
        question="Maya owns Nova.",
        answer="",
        evidence_text="",
        max_facts=5,
    )
    assert facts == []
