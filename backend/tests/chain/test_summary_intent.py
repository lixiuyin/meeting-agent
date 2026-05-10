"""Tests for summary intent detection and file summaries context."""

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

from unittest.mock import patch  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.services.rag._query import determine_adaptive_top_k, is_summary_intent  # noqa: E402


class TestSummaryIntentDetection:
    """Verify summary/overview queries are correctly detected."""

    def test_english_summarize(self):
        assert is_summary_intent("Please summarise the meeting")

    def test_english_summarize_with_z(self):
        assert is_summary_intent("Summarize the discussion")

    def test_english_overview(self):
        assert is_summary_intent("Give me an overview of all topics")

    def test_english_what_discussed(self):
        assert is_summary_intent("What was discussed in the meeting?")

    def test_english_compare(self):
        assert is_summary_intent("Compare the three meetings")

    def test_chinese_summary(self):
        assert is_summary_intent("总结这次会议")

    def test_chinese_overview(self):
        assert is_summary_intent("概述讨论了哪些内容")

    def test_chinese_topics(self):
        assert is_summary_intent("讲了什么主要内容")

    def test_chinese_compare(self):
        assert is_summary_intent("对比两个方案")

    def test_non_summary_question(self):
        assert not is_summary_intent("What is the project deadline?")

    def test_non_summary_short(self):
        assert not is_summary_intent("AI")

    def test_non_summary_factual(self):
        assert not is_summary_intent("Who mentioned the API redesign?")


class TestSummaryIntentTopK:
    """Verify summary intent raises top_k floor."""

    def test_summary_intent_gets_higher_floor(self):
        with patch.object(settings, "TOP_K", 5), patch.object(settings, "SUMMARY_INTENT_TOP_K", 12):
            k = determine_adaptive_top_k("Summarize all meetings", None, is_broad_recall=True)
            assert k >= 12

    def test_non_summary_broad_recall_floor_is_8(self):
        with patch.object(settings, "TOP_K", 5):
            k = determine_adaptive_top_k("project deadline", None, is_broad_recall=True)
            assert k >= 8
