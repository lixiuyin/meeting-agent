"""Tests for combined fact + entity extraction (P0-3)."""

import json
from unittest.mock import MagicMock

import pytest

from src.services.chain import _extraction as ex


def test_should_skip_extraction_on_empty_answer():
    assert ex.should_skip_extraction("hello?", "") is True


def test_should_skip_extraction_on_short_answer(monkeypatch):
    monkeypatch.setattr(ex.settings, "EXTRACTION_MIN_ANSWER_CHARS", 50)
    assert ex.should_skip_extraction("hi", "ok.") is True


def test_should_skip_extraction_passes_normal_answer(monkeypatch):
    monkeypatch.setattr(ex.settings, "EXTRACTION_MIN_ANSWER_CHARS", 10)
    assert ex.should_skip_extraction("q?", "This is a normal length answer.") is False


def test_truncate_for_extraction_noop_when_short():
    assert ex.truncate_for_extraction("short", 1000, "gpt-4o-mini") == "short"


def test_truncate_for_extraction_caps_long_text():
    huge = "word " * 10000
    out = ex.truncate_for_extraction(huge, 100, "gpt-4o-mini")
    assert out.endswith("[truncated]")
    assert len(out) < len(huge)


def test_parse_combined_valid_json():
    payload = json.dumps(
        {
            "facts": [{"key": "a", "value": "b"}],
            "entities": [{"name": "X", "type": "person"}],
            "relations": [],
        }
    )
    parsed = ex._parse_combined(payload)
    assert parsed is not None
    assert parsed["facts"][0]["key"] == "a"


def test_parse_combined_strips_fenced_code():
    payload = "```json\n" + json.dumps({"facts": [], "entities": [], "relations": []}) + "\n```"
    parsed = ex._parse_combined(payload)
    assert parsed == {"facts": [], "entities": [], "relations": []}


def test_parse_combined_returns_none_on_bad_json():
    assert ex._parse_combined("not json at all") is None


def test_parse_combined_returns_none_on_wrong_shape():
    assert ex._parse_combined('{"facts": "oops"}') is None


@pytest.mark.anyio
async def test_run_combined_extraction_skips_short_answer(monkeypatch):
    monkeypatch.setattr(ex.settings, "EXTRACTION_MIN_ANSWER_CHARS", 50)
    result = await ex.run_combined_extraction("u1", "q", "short")
    assert result == {"facts_added": 0, "entities_added": 0, "relations_added": 0}


@pytest.mark.anyio
async def test_run_combined_extraction_happy_path(monkeypatch):
    """Single LLM call produces facts + entities that get dispatched to storage."""
    monkeypatch.setattr(ex.settings, "EXTRACTION_MIN_ANSWER_CHARS", 5)

    llm_response = MagicMock()
    llm_response.content = json.dumps(
        {
            "facts": [
                {
                    "key": "user_uses_python_daily",
                    "value": "User uses Python every day for programming tasks",
                    "importance": 3,
                }
            ],
            "entities": [{"name": "Python", "type": "tool"}],
            "relations": [],
        }
    )

    from src.services.memory import memory_service

    monkeypatch.setattr(memory_service, "search_important", lambda *a, **k: [])
    set_calls = []
    monkeypatch.setattr(
        memory_service,
        "set",
        lambda *a, **k: set_calls.append((a, k)),
    )

    async def _fake_store_entities(user_id, entities, session_id, **kwargs):
        return len(entities)

    async def _fake_store_relations(user_id, relations, session_id, **kwargs):
        return len(relations)

    monkeypatch.setattr(
        "src.services.knowledge_graph._storage._store_entities", _fake_store_entities
    )
    monkeypatch.setattr(
        "src.services.knowledge_graph._storage._store_relations", _fake_store_relations
    )
    monkeypatch.setattr("src.services.knowledge_graph.settings.KNOWLEDGE_GRAPH_ENABLED", True)

    tpl = MagicMock()
    tpl.format.return_value = "PROMPT"

    async def _fake_to_thread(func, *args, **kwargs):
        return llm_response

    monkeypatch.setattr(
        "src.services.chain._extraction.get_llm", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "src.services.chain._extraction.get_combined_extraction_prompt",
        lambda: tpl,
        raising=False,
    )
    monkeypatch.setattr(
        "src.services.chain._extraction.cached_retry_invoke",
        lambda *a, **k: llm_response,
        raising=False,
    )
    monkeypatch.setattr("asyncio.to_thread", _fake_to_thread)

    result = await ex.run_combined_extraction(
        "u1",
        "Do you use Python for your daily programming?",
        "Yes, I use Python every day for programming tasks.",
    )

    assert result["facts_added"] >= 1
    assert result["entities_added"] == 1
    assert set_calls, "memory_service.set should have been called"


@pytest.mark.anyio
async def test_run_combined_extraction_handles_bad_llm_json(monkeypatch):
    """Unparseable LLM output returns zero counts without raising."""
    monkeypatch.setattr(ex.settings, "EXTRACTION_MIN_ANSWER_CHARS", 5)

    llm_response = MagicMock()
    llm_response.content = "not valid json"

    from src.services.memory import memory_service

    monkeypatch.setattr(memory_service, "search_important", lambda *a, **k: [])
    monkeypatch.setattr("src.services.knowledge_graph.settings.KNOWLEDGE_GRAPH_ENABLED", True)

    tpl = MagicMock()
    tpl.format.return_value = "PROMPT"

    async def _fake_to_thread(func, *args, **kwargs):
        return llm_response

    monkeypatch.setattr(
        "src.services.chain._extraction.get_llm", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "src.services.chain._extraction.get_combined_extraction_prompt",
        lambda: tpl,
        raising=False,
    )
    monkeypatch.setattr("asyncio.to_thread", _fake_to_thread)

    result = await ex.run_combined_extraction(
        "u1", "q", "a long enough answer to pass the skip check"
    )

    assert result == {"facts_added": 0, "entities_added": 0, "relations_added": 0}


def test_parse_combined_strips_thinking_prefix():
    """Reasoning models prefix the JSON with ``</think>`` (Qwen3-thinking, R1)."""
    payload = "</think>\n\n" + json.dumps(
        {"facts": [{"key": "a", "value": "b"}], "entities": [], "relations": []}
    )
    parsed = ex._parse_combined(payload)
    assert parsed is not None
    assert parsed["facts"][0]["key"] == "a"


def test_parse_combined_strips_full_thinking_block():
    payload = "<think>let me reason about this carefully</think>" + json.dumps(
        {"facts": [], "entities": [], "relations": []}
    )
    assert ex._parse_combined(payload) == {"facts": [], "entities": [], "relations": []}


def test_parse_combined_salvages_truncated_json():
    """Token-limit truncation mid-string should still recover complete prior items."""
    payload = (
        "</think>\n\n"
        "{\n"
        '  "facts": [\n'
        "    {\n"
        '      "key": "topic.llama_model.parameters",\n'
        '      "value": "650亿参数",\n'
        '      "importance": 4,\n'
        '      "category": "topic",\n'
        '      "ttl_days": 90\n'
        "    },\n"
        "    {\n"
        '      "key": "t'
    )
    parsed = ex._parse_combined(payload)
    assert parsed is not None
    assert len(parsed["facts"]) == 1
    assert parsed["facts"][0]["key"] == "topic.llama_model.parameters"
    assert parsed["entities"] == []
    assert parsed["relations"] == []


def test_salvage_returns_none_for_unrecoverable():
    assert ex._salvage_truncated_json("totally not json") is None
    assert ex._salvage_truncated_json("") is None
