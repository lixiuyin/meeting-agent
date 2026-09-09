"""Unit tests for LLM-as-judge JSON parsing and scoring logic."""

import pytest

from scripts._bench_rag_judge import (
    _average_precision_at_k,
    _call_judge,
    get_judge_config,
    judge_answer_correctness,
    judge_answer_relevance,
    judge_citation_quality,
    judge_context_precision,
    judge_context_recall,
    judge_faithfulness,
    judge_multi_turn_quality,
)


def test_judge_parses_valid_json(monkeypatch):
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = '{"score": 0.85, "justification": "Well supported."}'

            return _Resp()

    monkeypatch.setattr("scripts._bench_rag_judge.get_llm", lambda: _FakeLLM())
    result = judge_faithfulness("answer", "context")
    assert result is not None
    assert result["score"] == pytest.approx(0.85)
    assert result["justification"] == "Well supported."
    assert result["attempts"] == 1
    assert result["parse_retries"] == 0


def test_judge_parses_json_in_code_block(monkeypatch):
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = '```json\n{"score": 0.9, "justification": "Good"}\n```'

            return _Resp()

    monkeypatch.setattr("scripts._bench_rag_judge.get_llm", lambda: _FakeLLM())
    result = judge_answer_relevance("q", "a")
    assert result is not None
    assert result["score"] == pytest.approx(0.9)


def test_judge_returns_none_on_malformed_json(monkeypatch):
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = "this is not json"

            return _Resp()

    monkeypatch.setattr("scripts._bench_rag_judge.get_llm", lambda: _FakeLLM())
    result = _call_judge("prompt")
    assert result is None


def test_judge_returns_none_when_score_missing(monkeypatch):
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = '{"justification": "Missing score"}'

            return _Resp()

    monkeypatch.setattr("scripts._bench_rag_judge.get_llm", lambda: _FakeLLM())
    result = _call_judge("prompt")
    assert result is None


def test_judge_records_successful_strict_retry(monkeypatch):
    class _FakeLLM:
        calls = 0

        def invoke(self, prompt, **kwargs):
            self.calls += 1

            class _Resp:
                content = (
                    "not json"
                    if self.calls == 1
                    else '{"score": 0.7, "justification": "Recovered."}'
                )

            return _Resp()

    result = _call_judge("prompt", llm=_FakeLLM())
    assert result is not None
    assert result["attempts"] == 2
    assert result["parse_retries"] == 1


def test_multi_turn_judge_parses_all_four_metrics():
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = (
                    '{"faithfulness":{"score":1,"justification":"grounded"},'
                    '"appropriateness":{"score":0.9,"justification":"helpful"},'
                    '"naturalness":{"score":0.8,"justification":"coherent"},'
                    '"completeness":{"score":0.7,"justification":"mostly complete"}}'
                )

            return _Resp()

    result = judge_multi_turn_quality(
        history=[{"question": "What was blocked?", "answer": "The migration."}],
        question="Who owns it?",
        answer="Bob owns it.",
        context="Bob owns the database migration.",
        answerability="answerable",
        reference_answer="Bob.",
        expected_behavior=None,
        llm=_FakeLLM(),
    )

    assert result is not None
    assert result["metrics"]["faithfulness"]["score"] == 1.0
    assert result["metrics"]["completeness"]["score"] == 0.7
    assert result["parse_retries"] == 0


def test_multi_turn_judge_fails_closed_when_one_metric_is_missing():
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = (
                    '{"faithfulness":{"score":1,"justification":"ok"},'
                    '"appropriateness":{"score":1,"justification":"ok"},'
                    '"naturalness":{"score":1,"justification":"ok"}}'
                )

            return _Resp()

    result = judge_multi_turn_quality(
        history=[],
        question="Question",
        answer="Answer",
        context="Context",
        answerability="answerable",
        reference_answer="Reference",
        expected_behavior=None,
        llm=_FakeLLM(),
    )

    assert result is None


def test_context_precision_keeps_only_valid_chunk_indices():
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = (
                    '{"score": 0.5, "relevant_chunk_indices": '
                    '[2, 1, 2, 0, 4, true, "3"], "justification": "One relevant."}'
                )

            return _Resp()

    result = judge_context_precision("q", ["a", "b", "c"], llm=_FakeLLM())
    assert result is not None
    assert result["relevant_chunk_indices"] == [1, 2]
    assert result["judge_score"] == pytest.approx(0.5)
    assert result["score"] == pytest.approx(1.0)
    assert result["aggregation"] == "average_precision_at_k"


def test_average_precision_at_k_is_rank_sensitive_and_deduplicated():
    assert _average_precision_at_k([1, 3], chunk_count=5) == pytest.approx((1 + 2 / 3) / 2)
    assert _average_precision_at_k([3, 1, 3], chunk_count=5) == pytest.approx((1 + 2 / 3) / 2)
    assert _average_precision_at_k([], chunk_count=5) == 0.0


def test_judge_rejects_score_outside_unit_interval(monkeypatch):
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = '{"score": 1.5, "justification": "invalid scale"}'

            return _Resp()

    monkeypatch.setattr("scripts._bench_rag_judge.get_llm", lambda: _FakeLLM())
    assert _call_judge("prompt") is None


def test_correctness_and_citation_judges_accept_explicit_llm():
    prompts = []

    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            prompts.append(prompt)

            class _Resp:
                content = '{"score": 0.8, "justification": "supported"}'

            return _Resp()

    llm = _FakeLLM()
    correctness = judge_answer_correctness("q", "reference", "answer", llm=llm)
    citation = judge_citation_quality("claim [1]", ["support"], llm=llm)

    assert correctness is not None and correctness["score"] == pytest.approx(0.8)
    assert citation is not None and citation["score"] == pytest.approx(0.8)
    assert "<reference_answer>reference</reference_answer>" in prompts[0]
    assert "[1] support" in prompts[1]


def test_judge_context_recall_parses_json(monkeypatch):
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = '{"score": 0.75, "justification": "Most claims supported."}'

            return _Resp()

    monkeypatch.setattr("scripts._bench_rag_judge.get_llm", lambda: _FakeLLM())
    result = judge_context_recall("q", "ref", ["chunk1", "chunk2"])
    assert result is not None
    assert result["score"] == pytest.approx(0.75)
    assert result["justification"] == "Most claims supported."


def test_judge_context_recall_returns_none_on_bad_json(monkeypatch):
    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            class _Resp:
                content = "not json"

            return _Resp()

    monkeypatch.setattr("scripts._bench_rag_judge.get_llm", lambda: _FakeLLM())
    result = judge_context_recall("q", "ref", ["chunk"])
    assert result is None


def test_get_judge_config_includes_llm_and_embedder(monkeypatch):
    class _FakeSettings:
        LLM_BINDING = "openai"
        LLM_MODEL = "gpt-4o-mini"
        LLM_BASE_URL = "https://api.openai.com/v1"
        EMBEDDING_BINDING = "openai"
        EMBEDDING_MODEL = "text-embedding-3-small"
        EMBEDDING_BASE_URL = ""

    monkeypatch.setattr("src.core.config.settings", _FakeSettings(), raising=False)
    cfg = get_judge_config()
    assert cfg["llm"]["binding"] == "openai"
    assert cfg["llm"]["model"] == "gpt-4o-mini"
    assert cfg["llm"]["base_url_host"] == "api.openai.com"
    assert cfg["llm"]["reasoning_effort"] is None
    assert cfg["embedder"]["binding"] == "openai"
    assert cfg["embedder"]["model"] == "text-embedding-3-small"
    assert cfg["embedder"]["base_url_host"] == ""
