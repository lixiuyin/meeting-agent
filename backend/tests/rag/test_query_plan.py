import datetime

from src.services.rag._query_plan import (
    build_query_plan,
    infer_historical_cutoffs,
    infer_query_intent,
)


def test_query_plan_preserves_original_speaker_constraint() -> None:
    plan = build_query_plan(
        original_query="王明上周说了什么?",
        resolved_query="项目延期原因",
        variants=["交付风险"],
        known_speakers=["王明", "李华"],
    )
    assert plan.analysis.speaker_names == ["王明"]
    assert plan.semantic_queries == ("项目延期原因", "交付风险")
    assert "王明上周说了什么?" in plan.lexical_queries


def test_query_intents_are_explicit() -> None:
    assert infer_query_intent("总结所有会议") == "exhaustive"
    assert infer_query_intent("比较 A 与 B") == "comparison"
    assert infer_query_intent("What is the deadline?") == "factual"


def test_query_plan_infers_explicit_chinese_historical_cutoff() -> None:
    plan = build_query_plan(
        original_query="截至2025年3月1日, Orbit的负责人是谁?",
        resolved_query="Orbit 负责人",
    )

    assert plan.date_to == datetime.date(2025, 3, 1)


def test_explicit_api_cutoff_wins_over_natural_language_date() -> None:
    plan = build_query_plan(
        original_query="As of 2025-03-01, who owns Orbit?",
        resolved_query="Orbit owner",
        date_to=datetime.date(2025, 4, 2),
    )

    assert plan.date_to == datetime.date(2025, 4, 2)
    assert plan.historical_cutoffs == (datetime.date(2025, 3, 1),)


def test_comparison_preserves_each_snapshot_and_uses_latest_document_bound() -> None:
    question = "比较2025年1月1日的计划与截至2025年3月1日的项目状态"
    plan = build_query_plan(original_query=question, resolved_query="项目状态变化")

    assert infer_historical_cutoffs(question) == (
        datetime.date(2025, 1, 1),
        datetime.date(2025, 3, 1),
    )
    assert plan.historical_cutoffs == (
        datetime.date(2025, 1, 1),
        datetime.date(2025, 3, 1),
    )
    assert plan.date_to == datetime.date(2025, 3, 1)


def test_comparison_dates_do_not_require_redundant_as_of_wording() -> None:
    question = "比较2025-01-01与2025-03-01的项目状态"

    assert infer_historical_cutoffs(question) == (
        datetime.date(2025, 1, 1),
        datetime.date(2025, 3, 1),
    )


def test_exhaustive_comparison_keeps_both_historical_cutoffs() -> None:
    question = "比较2025-01-01与2025-03-01的所有项目状态"

    assert infer_query_intent(question) == "exhaustive"
    assert infer_historical_cutoffs(question) == (
        datetime.date(2025, 1, 1),
        datetime.date(2025, 3, 1),
    )


def test_invalid_or_unmarked_date_is_not_guessed() -> None:
    assert (
        build_query_plan(
            original_query="截至2025-02-30, 负责人是谁?",
            resolved_query="负责人",
        ).date_to
        is None
    )
    assert (
        build_query_plan(
            original_query="The meeting happened on 2025-03-01",
            resolved_query="meeting",
        ).date_to
        is None
    )
