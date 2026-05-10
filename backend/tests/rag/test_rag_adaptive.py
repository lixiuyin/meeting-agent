"""Tests for RAG adaptive top-k selection."""

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

from src.services import rag  # noqa: E402


class TestAdaptiveTopK:
    """Test adaptive top-k selection based on question complexity."""

    def test_simple_question_low_k(self):
        """Simple questions should return fewer documents."""
        assert rag.determine_adaptive_top_k("What is this?", None) == 3
        assert rag.determine_adaptive_top_k("Hello", None) == 3
        assert rag.determine_adaptive_top_k("Hi there!", None) == 3

    def test_complex_question_high_k(self):
        """Complex questions should return settings.TOP_K documents."""
        from src.core.config import settings

        complex_q = "Explain the detailed architecture and implementation strategy"
        assert rag.determine_adaptive_top_k(complex_q, None) == settings.TOP_K

    def test_user_override(self):
        """User-specified k should override adaptive selection."""
        assert rag.determine_adaptive_top_k("What is this?", 10) == 10
        assert rag.determine_adaptive_top_k("Complex question here", 5) == 5

    def test_medium_complexity_question(self):
        """Medium complexity questions return default k."""
        medium_q = "What were the main points discussed?"
        result = rag.determine_adaptive_top_k(medium_q, None)
        assert result in [5, 8]  # default TOP_K or high complexity
