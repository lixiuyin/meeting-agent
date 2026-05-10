"""Tests for LLM parsing utilities."""

import json

import pytest

from src.services.llm._parsing import parse_llm_json, strip_thinking_blocks


class TestStripThinkingBlocks:
    def test_removes_think_tags(self):
        text = "<think>Some reasoning</think>Final answer here"
        assert strip_thinking_blocks(text) == "Final answer here"

    def test_removes_thinking_tags(self):
        text = "<thinking>Plan step 1</thinking>Answer"
        assert strip_thinking_blocks(text) == "Answer"

    def test_removes_markdown_reasoning_section(self):
        text = "### Thinking: reason\n### Response: answer"
        assert strip_thinking_blocks(text) == "answer"

    def test_no_change_when_no_reasoning(self):
        text = "Just a normal answer."
        assert strip_thinking_blocks(text) == "Just a normal answer."

    def test_multiline_think_block(self):
        text = "<think>\nWait...\nRevised plan\n</think>\n\nThe answer is 42."
        assert strip_thinking_blocks(text) == "The answer is 42."


class TestParseLLMJson:
    def test_parses_json_directly(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_parses_json_from_code_fence(self):
        assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_parses_array(self):
        assert parse_llm_json("[1, 2, 3]") == [1, 2, 3]

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json("not json")
