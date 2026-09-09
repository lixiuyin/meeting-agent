# ruff: noqa: RUF001 -- Real Chinese punctuation is part of these regressions.
import pytest

from src.services.rag._query import _is_simple_query, is_fast_query


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
        "What did he decide?",
        "Compare the meetings",
    ],
)
def test_contextual_and_analytical_queries_never_skip_resolver(question):
    assert not _is_simple_query(question)
    assert not is_fast_query(question, include_summary=True)


@pytest.mark.parametrize("question", ["Atlas项目负责人是谁？", "Who owns Atlas?"])
def test_standalone_fact_queries_remain_simple(question):
    assert _is_simple_query(question)
