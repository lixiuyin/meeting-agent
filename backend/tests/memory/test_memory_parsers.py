"""Tests for memory consolidation JSON parsers."""

from src.services.memory import _parse_consolidation_json


class TestParseConsolidationJson:
    def test_parses_valid_json(self):
        raw = '{"key": "user_language", "value": "English", "importance": 4, "category": "preference"}'
        result = _parse_consolidation_json(raw)
        assert result is not None
        assert result["key"] == "user_language"
        assert result["value"] == "English"

    def test_strips_markdown_fences(self):
        raw = '```json\n{"key": "foo", "value": "bar"}\n```'
        result = _parse_consolidation_json(raw)
        assert result is not None
        assert result["key"] == "foo"

    def test_returns_none_for_missing_key(self):
        raw = '{"value": "something"}'
        assert _parse_consolidation_json(raw) is None

    def test_returns_none_for_invalid_json(self):
        assert _parse_consolidation_json("not json") is None

    def test_returns_none_for_empty_string(self):
        assert _parse_consolidation_json("") is None
