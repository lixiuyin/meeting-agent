# ruff: noqa: RUF001 -- Real Chinese punctuation is part of these regressions.
import pytest

from src.services.rag._query import (
    _is_simple_query,
    classify_query_route,
    is_fast_query,
    validate_fast_path_evidence,
)


@pytest.mark.parametrize(
    "question",
    [
        "他负责什么？",
        "那截止日期呢？",
        "她的任务是什么？",
        "这些任务完成了吗？",
        "请比较这三场会议中负责人和截止时间的变化，并解释为什么项目延期。",
        "比较三场会议",
        "为什么延期？",
        "GPT的后训练是怎么做的？",
        "GPT 的后训练是如何进行的？",
        "What did he decide?",
        "Compare the meetings",
        "How does post-training work?",
        "How is GPT post-training done?",
    ],
)
def test_contextual_and_analytical_queries_never_skip_resolver(question):
    assert not _is_simple_query(question)
    assert not is_fast_query(question, include_summary=True)


@pytest.mark.parametrize("question", ["Atlas项目负责人是谁？", "Who owns Atlas?"])
def test_standalone_fact_queries_remain_simple(question):
    assert _is_simple_query(question)


@pytest.mark.parametrize(
    ("question", "expected_route", "answer_type"),
    [
        ("Who owns Atlas?", "atomic_fact", "person"),
        ("Atlas项目负责人是谁？", "atomic_fact", "person"),
        ("What is the deadline?", "atomic_fact", "date"),
        ("How many action items are open?", "atomic_fact", "number"),
        ("Who owns Atlas and why?", "analytical_synthesis", "explanation"),
        ("Who owns Atlas and what deadline?", "bounded_synthesis", "explanation"),
        ("GPT的后训练是怎么做的？", "analytical_synthesis", "explanation"),
        ("How does post-training work?", "analytical_synthesis", "explanation"),
    ],
)
def test_route_classifier_uses_answer_shape_not_only_length(
    question,
    expected_route,
    answer_type,
):
    decision = classify_query_route(question)
    assert decision.route == expected_route
    assert decision.answer_type == answer_type


def test_evidence_filter_accepts_concentrated_atomic_evidence():
    decision = classify_query_route("Who owns Atlas?")
    evidence = validate_fast_path_evidence(
        decision,
        [
            {
                "content": "Priya Nair owns Atlas.",
                "score": 4.2,
                "metadata": {"meeting_id": 1, "file_id": 2},
            },
            {
                "content": "Atlas ownership was assigned to Priya Nair.",
                "score": 2.0,
                "metadata": {"meeting_id": 1, "file_id": 2},
            },
        ],
    )
    assert evidence.safe


def test_evidence_filter_promotes_cross_source_ambiguity():
    decision = classify_query_route("Who owns Atlas?")
    evidence = validate_fast_path_evidence(
        decision,
        [
            {
                "content": "Priya owns Atlas.",
                "score": 4.2,
                "metadata": {"meeting_id": 1, "file_id": 2},
            },
            {
                "content": "Lee owns Atlas.",
                "score": 4.0,
                "metadata": {"meeting_id": 3, "file_id": 4},
            },
        ],
    )
    assert not evidence.safe
    assert evidence.reason == "evidence_spans_sources"


def test_evidence_filter_requires_expected_date_shape():
    decision = classify_query_route("What is the deadline?")
    evidence = validate_fast_path_evidence(
        decision,
        [
            {
                "content": "The deadline will be decided later.",
                "score": 3.0,
                "metadata": {"meeting_id": 1, "file_id": 2},
            }
        ],
    )
    assert not evidence.safe
    assert evidence.reason == "missing_date_answer_shape"
