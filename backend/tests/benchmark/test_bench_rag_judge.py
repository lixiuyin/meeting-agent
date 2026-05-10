"""Unit tests for LLM-as-judge JSON parsing and scoring logic."""

import pytest

from scripts._bench_rag_judge import (
    _call_judge,
    get_judge_config,
    judge_answer_relevance,
    judge_context_recall,
    judge_faithfulness,
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
    assert cfg["embedder"]["binding"] == "openai"
    assert cfg["embedder"]["model"] == "text-embedding-3-small"
    assert cfg["embedder"]["base_url_host"] == ""
