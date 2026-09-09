"""Tests for paired long-horizon memory evaluation."""

import json
from pathlib import Path

import pytest

from scripts._bench_kg_memory import (
    build_knowledge_graph_answer_prompt,
    execute_knowledge_graph_cases,
    validate_knowledge_graph_dataset,
)
from scripts._bench_memory import (
    _record_evidence_supports_value,
    _record_supports_event,
    build_memory_answer_prompt,
    execute_memory_cases,
    execute_memory_pipeline_cases,
    validate_memory_dataset,
)

DATASET_PATH = Path(__file__).parents[2] / "evaluation" / "datasets" / "memory_cases.json"
KG_DATASET_PATH = (
    Path(__file__).parents[2] / "evaluation" / "datasets" / "knowledge_graph_cases.json"
)


def test_repository_memory_dataset_is_valid() -> None:
    validate_memory_dataset(json.loads(DATASET_PATH.read_text(encoding="utf-8")))
    validate_knowledge_graph_dataset(json.loads(KG_DATASET_PATH.read_text(encoding="utf-8")))


def test_memory_answer_prompt_contains_no_gold_answer() -> None:
    prompt = build_memory_answer_prompt("When?", ["The date is March 3."])

    assert "March 3" in prompt
    assert "reference_answer" not in prompt
    assert "do not guess" in prompt

    kg_prompt = build_knowledge_graph_answer_prompt("When?", "- concept: April 12")
    assert "April 12" in kg_prompt
    assert "reference_answer" not in kg_prompt
    assert "do not guess" in kg_prompt


def test_pipeline_evidence_accepts_grounded_object_but_not_wrong_value() -> None:
    assert _record_evidence_supports_value(
        {
            "value": "Omar (replacing Nina)",
            "object_value": "Omar",
            "evidence_excerpt": "Omar replaced Nina as owner of the incident review.",
        }
    )
    assert _record_evidence_supports_value(
        {
            "value": "unknown/not assigned",
            "object_value": "unknown/not assigned",
            "evidence_excerpt": "The owner is explicitly unknown and has not been assigned.",
        }
    )
    assert not _record_evidence_supports_value(
        {
            "value": "Bob",
            "object_value": "Bob",
            "evidence_excerpt": "Alice owns the incident review.",
        }
    )
    assert not _record_evidence_supports_value(
        {
            "key": "project.orbit.owner",
            "value": "Alice",
            "object_value": "Alice",
            "evidence_excerpt": "Bob replaced Alice as owner of Orbit.",
        }
    )
    assert not _record_evidence_supports_value(
        {
            "value": "Bob owns Orbit",
            "object_value": "Alice",
            "evidence_excerpt": "Alice owns Orbit",
        }
    )


def test_pipeline_event_scoring_uses_structured_current_value() -> None:
    record = {
        "value": "Omar (replacing Nina)",
        "object_value": "Omar",
    }
    prior = {"expected_value_terms": ["Nina"], "expected_negative": False}
    latest = {"expected_value_terms": ["Omar"], "expected_negative": False}

    assert not _record_supports_event(record, prior)
    assert _record_supports_event(record, latest)


def test_pipeline_event_scoring_handles_unknown_alias_and_secondary_atoms() -> None:
    assert _record_supports_event(
        {
            "key": "project.orbit.release_owner",
            "value": "Unknown / not assigned",
            "object_value": "unassigned",
        },
        {"expected_value_terms": ["unknown"], "expected_negative": True},
    )
    assert _record_supports_event(
        {
            "key": "decision.storage_budget.approval",
            "value": "Finance approved the storage budget on January 20",
            "object_value": "Finance",
        },
        {"expected_value_terms": ["January 20"], "expected_negative": False},
    )


def test_pipeline_owner_scoring_distinguishes_owner_from_owned_target() -> None:
    record = {
        "key": "project.security_review.threat_model.owner",
        "value": "Alex owns the threat model in the Security Review",
        "object_value": "Alex",
    }
    assert _record_supports_event(
        record,
        {"expected_value_terms": ["Alex"], "expected_negative": False},
    )
    assert _record_supports_event(
        record,
        {"expected_value_terms": ["threat model"], "expected_negative": False},
    )
    assert _record_supports_event(
        {
            "key": "person.alex.mobile_ui_spec_owner",
            "value": "mobile UI specification",
            "subject": "mobile UI specification",
            "object_value": "Alex",
        },
        {"expected_value_terms": ["mobile UI specification"], "expected_negative": False},
    )
    assert not _record_supports_event(
        {
            "key": "project.orbit.owner",
            "value": "Bob owns Orbit",
            "object_value": "Alice",
        },
        {"expected_value_terms": ["Bob"], "expected_negative": False},
    )
    assert not _record_supports_event(
        {
            "key": "project.security_review.threat_model.owner",
            "project_id": "security_review",
            "value": "Alex owns the threat model",
            "object_value": "Alex",
        },
        {
            "key": "project.design_review.alex_role",
            "expected_value_terms": ["Alex"],
            "expected_negative": False,
        },
    )


@pytest.mark.asyncio
async def test_pipeline_benchmark_penalizes_one_object_containing_old_and_new_values() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "mixed-owner",
                "competency": "selective_forgetting",
                "events": [
                    {
                        "sequence": 1,
                        "key": "owner",
                        "fact": "Nina owns the review",
                        "expected_value_terms": ["Nina"],
                    },
                    {
                        "sequence": 2,
                        "key": "owner",
                        "fact": "Omar owns the review",
                        "expected_value_terms": ["Omar"],
                    },
                ],
                "expected_memory_keys": ["owner"],
                "query": "Who owns the review?",
                "expected_answer": "Omar",
            }
        ],
    }

    async def extract_fn(**_kwargs):
        return {"facts_added": 1}

    async def list_fn(**_kwargs):
        return [
            {
                "key": "owner",
                "value": "Nina and Omar own the review",
                "object_value": "Nina and Omar",
                "assertion_status": "confirmed",
                "evidence_excerpt": "Nina and Omar own the review",
            }
        ]

    result = await execute_memory_pipeline_cases(dataset, extract_fn=extract_fn, list_fn=list_fn)

    assert result["stats"]["pipeline_latest_value_accuracy"] == 0.0
    assert result["rows"][0]["stale_confirmed_values"] == {
        "owner": ["Nina and Omar own the review"]
    }


@pytest.mark.asyncio
async def test_pipeline_benchmark_measures_write_and_update_path() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "owner-update",
                "competency": "selective_forgetting",
                "events": [
                    {
                        "sequence": 1,
                        "key": "project.atlas.owner",
                        "fact": "Alice owns Atlas.",
                        "expected_value_terms": ["Alice"],
                    },
                    {
                        "sequence": 2,
                        "key": "project.atlas.owner",
                        "fact": "Bob owns Atlas.",
                        "expected_value_terms": ["Bob"],
                    },
                ],
                "expected_memory_keys": ["project.atlas.owner"],
                "expected_physical_keys": ["project.atlas.owner"],
                "query": "Who owns Atlas?",
                "expected_answer": "Bob",
            }
        ],
    }
    stores: dict[str, dict[str, dict]] = {}

    async def extract_fn(*, user_id, event):
        stores.setdefault(user_id, {})[event["key"]] = {
            "key": event["key"],
            "value": event["fact"],
            "assertion_status": "confirmed",
            "evidence_excerpt": event["fact"],
            "revision": event["sequence"],
        }
        return {"facts_added": 1, "facts_candidates": 1, "facts_rejected": 0}

    async def list_fn(*, user_id):
        return list(stores.get(user_id, {}).values())

    result = await execute_memory_pipeline_cases(
        dataset,
        extract_fn=extract_fn,
        list_fn=list_fn,
    )

    assert result["valid"] is True
    assert result["complete"] is True
    assert result["stats"]["pipeline_write_recall"] == 1.0
    assert result["stats"]["pipeline_reference_key_agreement"] == 1.0
    assert result["rows"][0]["stored"][0]["value"] == "Bob owns Atlas."
    assert result["rows"][0]["event_diagnostics"][0]["result"]["facts_added"] == 1
    assert result["stats"]["pipeline_latest_value_accuracy"] == 1.0
    assert result["stats"]["pipeline_confirmed_evidence_rate"] == 1.0
    assert result["stats"]["pipeline_events_persisted"] == 2


@pytest.mark.asyncio
async def test_pipeline_superseded_outcome_requires_observed_retirement() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "owner-replacement",
                "competency": "selective_forgetting",
                "events": [
                    {
                        "sequence": 1,
                        "key": "owner",
                        "fact": "Alice owns Atlas.",
                        "expected_value_terms": ["Alice"],
                    },
                    {
                        "sequence": 2,
                        "key": "owner",
                        "fact": "Bob owns Atlas.",
                        "expected_value_terms": ["Bob"],
                        "expected_outcome": "superseded",
                    },
                ],
                "expected_memory_keys": ["owner"],
                "query": "Who owns Atlas?",
                "expected_answer": "Bob",
            }
        ],
    }
    stores: dict[str, dict] = {}

    async def extract_fn(*, user_id, event):
        stores[user_id] = {
            "key": "owner",
            "value": event["fact"],
            "assertion_status": "confirmed",
            "evidence_excerpt": event["fact"],
            "revision": event["sequence"],
        }
        return {"facts_added": 1}

    async def list_fn(*, user_id):
        current = stores.get(user_id)
        return [dict(current)] if current else []

    result = await execute_memory_pipeline_cases(dataset, extract_fn=extract_fn, list_fn=list_fn)

    assert result["valid"] is True
    assert result["rows"][0]["event_diagnostics"][1]["retirement_observed"] is True


@pytest.mark.asyncio
async def test_pipeline_superseded_outcome_rejects_parallel_unretired_fact() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "owner-not-retired",
                "competency": "selective_forgetting",
                "events": [
                    {
                        "sequence": 1,
                        "key": "owner",
                        "fact": "Alice owns Atlas.",
                        "expected_value_terms": ["Alice"],
                    },
                    {
                        "sequence": 2,
                        "key": "owner",
                        "fact": "Bob owns Atlas.",
                        "expected_value_terms": ["Bob"],
                        "expected_outcome": "superseded",
                    },
                ],
                "expected_memory_keys": ["owner"],
                "query": "Who owns Atlas?",
                "expected_answer": "Bob",
            }
        ],
    }
    records: list[dict] = []

    async def extract_fn(*, event, **_kwargs):
        records.append(
            {
                "key": f"owner.{event['sequence']}",
                "value": event["fact"],
                "assertion_status": "confirmed",
                "evidence_excerpt": event["fact"],
                "revision": 1,
            }
        )
        return {"facts_added": 1}

    async def list_fn(**_kwargs):
        return [dict(record) for record in records]

    result = await execute_memory_pipeline_cases(dataset, extract_fn=extract_fn, list_fn=list_fn)

    assert result["valid"] is False
    assert result["rows"][0]["event_diagnostics"][1]["retirement_observed"] is False


@pytest.mark.asyncio
async def test_pipeline_superseded_outcome_rejects_unrelated_retirement() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "unrelated-retirement",
                "competency": "selective_forgetting",
                "events": [
                    {
                        "sequence": 1,
                        "key": "owner",
                        "fact": "Alice owns Atlas.",
                        "expected_value_terms": ["Alice"],
                    },
                    {
                        "sequence": 2,
                        "key": "owner",
                        "fact": "Bob owns Atlas.",
                        "expected_value_terms": ["Bob"],
                        "expected_outcome": "superseded",
                    },
                ],
                "expected_memory_keys": ["owner"],
                "query": "Who owns Atlas?",
                "expected_answer": "Bob",
            }
        ],
    }
    state = {"step": 0}

    async def extract_fn(**_kwargs):
        state["step"] += 1
        return {"facts_added": 1}

    async def list_fn(**_kwargs):
        if state["step"] <= 1:
            return (
                [
                    {
                        "key": "owner",
                        "value": "Alice owns Atlas.",
                        "assertion_status": "confirmed",
                        "revision": 1,
                    }
                ]
                if state["step"]
                else []
            )
        return [
            {
                "key": "owner.new",
                "value": "Bob owns Atlas.",
                "assertion_status": "confirmed",
                "revision": 1,
            },
            {
                "key": "budget",
                "value": "Budget approved.",
                "assertion_status": "retracted",
                "revision": 2,
                "superseded_by": "budget.new",
            },
        ]

    result = await execute_memory_pipeline_cases(dataset, extract_fn=extract_fn, list_fn=list_fn)
    assert result["valid"] is False
    assert result["rows"][0]["event_diagnostics"][1]["retirement_observed"] is False


@pytest.mark.asyncio
async def test_pipeline_benchmark_rejects_stale_active_fact_and_evidence_only_match() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "owner-update",
                "competency": "selective_forgetting",
                "events": [
                    {"sequence": 1, "key": "owner", "fact": "Nina owns incident review."},
                    {"sequence": 2, "key": "owner", "fact": "Omar owns incident review."},
                ],
                "expected_memory_keys": ["owner"],
                "query": "Who owns incident review?",
                "expected_answer": "Omar",
            }
        ],
    }

    async def extract_fn(*, user_id, event):
        return {"facts_added": 1}

    async def list_fn(*, user_id):
        return [
            {
                "key": "legacy.nina.role",
                "value": "Nina owns incident review.",
                "assertion_status": "confirmed",
                "evidence_excerpt": "Nina owns incident review.",
            },
            {
                "key": "topic.incident_review.owner",
                "value": "wrong stored value",
                "assertion_status": "confirmed",
                "evidence_excerpt": "Omar owns incident review.",
            },
        ]

    result = await execute_memory_pipeline_cases(dataset, extract_fn=extract_fn, list_fn=list_fn)

    assert result["stats"]["pipeline_latest_value_accuracy"] == 0.0
    assert result["stats"]["pipeline_confirmed_evidence_rate"] == 0.5
    assert result["rows"][0]["stale_confirmed_values"] == {"owner": ["Nina owns incident review."]}
    assert not result["valid"]
    assert not result["complete"]


@pytest.mark.asyncio
async def test_pipeline_benchmark_is_incomplete_when_extraction_persists_nothing() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "miss",
                "competency": "test_time_learning",
                "events": [{"sequence": 1, "key": "date", "fact": "Launch is March 3."}],
                "expected_memory_keys": ["date"],
                "expected_physical_keys": ["date"],
                "query": "When?",
                "expected_answer": "March 3",
            }
        ],
    }

    async def extract_fn(*, user_id, event):
        return {"facts_added": 0}

    async def list_fn(*, user_id):
        return []

    result = await execute_memory_pipeline_cases(dataset, extract_fn=extract_fn, list_fn=list_fn)
    assert result["valid"] is False
    assert result["complete"] is False
    assert result["stats"]["pipeline_events_persisted"] == 0


@pytest.mark.asyncio
async def test_pipeline_benchmark_does_not_credit_correct_key_with_wrong_value() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "wrong-value",
                "competency": "test_time_learning",
                "events": [
                    {
                        "sequence": 1,
                        "key": "date",
                        "fact": "Launch is March 3.",
                        "expected_value_terms": ["March 3"],
                        "expected_negative": False,
                    }
                ],
                "expected_memory_keys": ["date"],
                "expected_physical_keys": ["date"],
                "query": "When?",
                "expected_answer": "March 3",
            }
        ],
    }

    async def extract_fn(**kwargs):
        return {"facts_added": 1}

    async def list_fn(**kwargs):
        return [
            {
                "key": "date",
                "value": "April 9",
                "assertion_status": "confirmed",
                "evidence_excerpt": "Launch is March 3.",
            }
        ]

    result = await execute_memory_pipeline_cases(dataset, extract_fn=extract_fn, list_fn=list_fn)
    assert result["stats"]["pipeline_write_recall"] == 0.0
    assert result["stats"]["pipeline_latest_value_accuracy"] == 0.0
    assert result["stats"]["pipeline_reference_key_agreement"] == 1.0


@pytest.mark.asyncio
async def test_execute_knowledge_graph_cases_measures_multihop_gain() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_knowledge_graph_off_vs_on",
        "cases": [
            {
                "id": "two-hop",
                "conditions": ["knowledge_graph_off", "knowledge_graph_on"],
                "entities": [
                    {"name": "Alice", "type": "person"},
                    {"name": "Atlas", "type": "project"},
                    {"name": "Helios", "type": "concept", "description": "April 12"},
                ],
                "relations": [
                    {"subject": "Alice", "predicate": "leads", "object": "Atlas"},
                    {"subject": "Atlas", "predicate": "uses", "object": "Helios"},
                ],
                "query": "When was Alice's initiative approved?",
                "expected_answer": "April 12",
                "expected_context_terms": ["alice", "atlas", "helios", "april 12"],
            }
        ],
    }

    async def store_graph_fn(**kwargs):
        return {"entities_added": 3, "relations_added": 2}

    async def retrieve_context_fn(**kwargs):
        return "Alice leads Atlas; Atlas uses Helios; Helios was approved April 12."

    async def answer_fn(*, query, entity_context):
        return "April 12" if entity_context else "Insufficient context"

    def judge_fn(*, question, reference_answer, answer):
        return {
            "score": float(reference_answer in answer),
            "justification": "deterministic test",
            "parse_retries": 0,
        }

    result = await execute_knowledge_graph_cases(
        dataset,
        store_graph_fn=store_graph_fn,
        retrieve_context_fn=retrieve_context_fn,
        answer_fn=answer_fn,
        judge_fn=judge_fn,
        judge_repeats=3,
    )

    assert result["valid"] is True
    assert result["complete"] is True
    assert result["stats"]["knowledge_graph_context_recall"] == 1.0
    assert result["stats"]["knowledge_graph_multihop"] == 1.0
    assert result["stats"]["knowledge_graph_gain"] == 1.0
    assert all(len(row["judge_diagnostics"]) == 3 for row in result["rows"])


@pytest.mark.asyncio
async def test_execute_memory_cases_measures_paired_gain_and_selective_forgetting() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "update",
                "competency": "selective_forgetting",
                "events": [
                    {"sequence": 1, "key": "date", "fact": "February 15"},
                    {"sequence": 2, "key": "date", "fact": "March 3"},
                ],
                "expected_memory_keys": ["date"],
                "conditions": ["memory_off", "distractor_only", "memory_on"],
                "distractor_events": 2,
                "query": "What is the latest date?",
                "expected_answer": "March 3",
                "stale_answer": "February 15",
            }
        ],
    }
    stores: dict[str, dict[str, str]] = {}

    async def remember_fn(*, user_id, key, value, sequence):
        stores.setdefault(user_id, {})[key] = value

    async def retrieve_fn(*, user_id, query, limit):
        return [
            {"key": key, "value": value, "combined_score": 1.0}
            for key, value in stores.get(user_id, {}).items()
        ][:limit]

    async def answer_fn(*, query, memory_values):
        return "March 3" if "March 3" in memory_values else "Insufficient context"

    def judge_fn(*, question, reference_answer, answer):
        return {
            "score": float(reference_answer in answer),
            "justification": "deterministic test",
            "parse_retries": 0,
        }

    result = await execute_memory_cases(
        dataset,
        remember_fn=remember_fn,
        retrieve_fn=retrieve_fn,
        answer_fn=answer_fn,
        judge_fn=judge_fn,
        judge_repeats=3,
    )

    assert result["valid"] is True
    assert result["stats"]["accurate_retrieval"] == 1.0
    assert result["stats"]["test_time_learning"] == 1.0
    assert result["stats"]["selective_forgetting"] == 1.0
    assert result["stats"]["memory_gain"] == 1.0
    assert result["stats"]["distractor_control_margin"] == 1.0
    on_row = next(row for row in result["rows"] if row["condition"] == "memory_on")
    assert [item["value"] for item in on_row["retrieved_memories"] if item["key"] == "date"] == [
        "March 3"
    ]
    assert len(on_row["judge_diagnostics"]) == 3


@pytest.mark.asyncio
async def test_execute_memory_cases_fails_validity_on_judge_parse_failure() -> None:
    dataset = {
        "schema_version": 1,
        "comparison": "paired_same_reasoner_memory_off_vs_memory_on",
        "cases": [
            {
                "id": "case",
                "competency": "accurate_retrieval",
                "events": [{"sequence": 1, "key": "fact", "fact": "value"}],
                "expected_memory_keys": ["fact"],
                "query": "Question?",
                "expected_answer": "value",
            }
        ],
    }

    async def remember_fn(**kwargs):
        return None

    async def retrieve_fn(**kwargs):
        return [{"key": "fact", "value": "value"}]

    async def answer_fn(**kwargs):
        return "value"

    result = await execute_memory_cases(
        dataset,
        remember_fn=remember_fn,
        retrieve_fn=retrieve_fn,
        answer_fn=answer_fn,
        judge_fn=lambda **kwargs: None,
    )

    assert result["valid"] is False
    assert result["stats"]["parse_failures"] == 2
    assert len(result["validity_errors"]) == 2


def test_owner_and_target_atoms_may_span_authoritative_fields():
    assert _record_supports_event(
        {
            "key": "project.orbit.owner",
            "project_id": "orbit",
            "subject": "Orbit",
            "object_value": "Alice",
            "value": "Alice",
        },
        {"key": "project.orbit.owner", "expected_value_terms": ["Alice", "Orbit"]},
    )
    assert not _record_supports_event(
        {
            "key": "project.orbit.owner",
            "subject": "Orbit",
            "object_value": "Bob",
            "value": "Bob replaced Alice as owner of Orbit",
        },
        {"key": "project.orbit.owner", "expected_value_terms": ["Alice", "Orbit"]},
    )


def test_policy_scope_requires_subject_but_does_not_invent_project():
    event = {
        "key": "project.billing.retention_policy",
        "expected_project_id": None,
        "expected_subject_terms": ["billing log"],
        "expected_value_terms": ["90 days"],
    }
    record = {
        "key": "policy.billing_logs.retention_period",
        "subject": "billing_logs",
        "value": "90 days",
        "object_value": "90 days",
    }
    assert _record_supports_event(record, event)
    assert not _record_supports_event({**record, "subject": "security_logs"}, event)
    assert not _record_supports_event(
        {**record, "value": "30 days", "object_value": "30 days"}, event
    )
    assert not _record_supports_event(record, {**event, "expected_project_id": "design_review"})


def test_owner_evidence_uses_structured_target_without_laundering_display():
    record = {
        "key": "project.design_review.spec.owner",
        "subject": "mobile UI specification",
        "object_value": "Alex",
        "value": "Alex owns the mobile UI specification in the Design Review",
        "evidence_excerpt": "In the Design Review, Alex owns the mobile UI specification.",
    }
    assert _record_evidence_supports_value(record)
    assert not _record_evidence_supports_value({**record, "object_value": "Bob"})
    assert not _record_evidence_supports_value({**record, "subject": "threat model"})
    assert not _record_evidence_supports_value(
        {**record, "value": "Alex owns the mobile UI specification and production passwords"}
    )
