"""Tests for knowledge graph JSON parsers."""

import json

from src.services.knowledge_graph import _parse_entities_json


class TestParseEntitiesJson:
    def test_parses_valid_json(self):
        raw = json.dumps(
            {
                "entities": [{"name": "Alice", "type": "person"}],
                "relations": [{"subject": "Alice", "predicate": "works_on", "object": "ProjectX"}],
            }
        )
        result = _parse_entities_json(raw)
        assert result is not None
        assert result["entities"][0]["name"] == "Alice"
        assert result["relations"][0]["predicate"] == "works_on"

    def test_strips_markdown_fences(self):
        raw = '```json\n{"entities": [], "relations": []}\n```'
        result = _parse_entities_json(raw)
        assert result is not None
        assert result["entities"] == []

    def test_returns_none_for_invalid_json(self):
        assert _parse_entities_json("not json at all") is None

    def test_returns_none_for_empty_string(self):
        assert _parse_entities_json("") is None

    def test_fills_missing_keys(self):
        """A dict with only 'entities' still parses (relations defaults to [])."""
        raw = json.dumps({"entities": [{"name": "Zoom", "type": "tool"}]})
        result = _parse_entities_json(raw)
        assert result is not None
        assert result["relations"] == []
