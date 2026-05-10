"""Tests for context formatting helpers — XML tag changes for non-citable sections."""

import os
import tempfile
from pathlib import Path

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services.chain._formatting import _build_system_context  # noqa: E402


class TestBuildSystemContextXmlTags:
    """Verify that non-citable sections use XML tags, not bracket labels."""

    def test_memory_section_uses_xml_tags(self):
        result = _build_system_context(
            memory_context="user likes Python",
            session_context="",
            entity_context="",
            meeting_context="",
            web_context="",
        )
        assert "<user_memory>" in result
        assert "</user_memory>" in result
        assert "[User Memory]" not in result

    def test_entity_merged_into_memory_section(self):
        result = _build_system_context(
            memory_context="user prefers dark mode",
            session_context="",
            entity_context="entity: Alice (person)",
            meeting_context="",
            web_context="",
        )
        assert "<user_memory>" in result
        assert "user prefers dark mode" in result
        assert "entity: Alice (person)" in result

    def test_session_section_uses_xml_tags(self):
        result = _build_system_context(
            memory_context="",
            session_context="previous discussion about API design",
            entity_context="",
            meeting_context="",
            web_context="",
        )
        assert "<prior_conversations>" in result
        assert "</prior_conversations>" in result
        assert "[Prior Conversations]" not in result

    def test_web_section_uses_xml_tags(self):
        result = _build_system_context(
            memory_context="",
            session_context="",
            entity_context="",
            meeting_context="",
            web_context="search result: Python 3.13 released",
        )
        assert "<web_search>" in result
        assert "</web_search>" in result
        assert "[Web Search]" not in result

    def test_meeting_content_uses_bracket_label(self):
        """Meeting content keeps bracket label — its [1], [2] are real citations."""
        result = _build_system_context(
            memory_context="",
            session_context="",
            entity_context="",
            meeting_context="[1] Meeting about AI\nAI is important",
            web_context="",
        )
        assert "[Meeting Content]" in result
        assert "[1] Meeting about AI" in result

    def test_all_sections_combined(self):
        result = _build_system_context(
            memory_context="user likes Python",
            session_context="discussed APIs before",
            entity_context="entity: Bob",
            meeting_context="[1] meeting content",
            web_context="search result",
        )
        assert "<user_memory>" in result
        assert "<prior_conversations>" in result
        assert "<web_search>" in result
        assert "[Meeting Content]" in result
        # None of the old bracket labels should appear
        assert "[User Memory]" not in result
        assert "[Prior Conversations]" not in result
        assert "[Web Search]" not in result

    def test_empty_context(self):
        result = _build_system_context(
            memory_context="",
            session_context="",
            entity_context="",
            meeting_context="",
            web_context="",
        )
        assert result == "No context available."

    def test_no_meeting_content_placeholder_excluded(self):
        result = _build_system_context(
            memory_context="some memory",
            session_context="",
            entity_context="",
            meeting_context="No relevant meeting content found.",
            web_context="",
        )
        assert "[Meeting Content]" not in result
        assert "<user_memory>" in result
