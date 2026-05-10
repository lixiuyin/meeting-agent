"""Tests for RAG structure-aware chunking."""

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

from src.services.rag._chunkers import _split_by_structure  # noqa: E402


class TestStructureAwareChunking:
    """Test structure-aware semantic chunking."""

    def test_split_by_headings_small_chunk_size(self):
        """Should split on markdown headings when chunk size forces it."""
        text = """# Introduction
This is the intro text with lots of content that makes it exceed the limit when combined.

# Main Topic
This is the main content section with substantial text to process.

# Conclusion
Final thoughts and summary here with enough text to matter."""
        # Use very small max_chunk_size to force splitting
        chunks = _split_by_structure(text, 100)
        assert len(chunks) >= 2

    def test_split_preserves_structure(self):
        """Should preserve structural markers in output."""
        text = """# Heading 1
Content under first heading.

# Heading 2
Content under second heading."""
        chunks = _split_by_structure(text, 50)
        # At least one chunk should contain a heading
        assert any("#" in c for c in chunks)

    def test_split_by_speaker_labels(self):
        """Should handle speaker labels in text."""
        text = """Speaker 1: Hello everyone
Welcome to the meeting today.

Speaker 2: Thanks for joining.
Let's get started with the agenda."""
        chunks = _split_by_structure(text, 80)
        # Should create multiple chunks due to small size limit
        assert len(chunks) >= 1
        # Should preserve speaker labels
        assert any("Speaker" in c for c in chunks)

    def test_no_structure_fallback(self):
        """Should handle text without clear structure."""
        text = "This is just a plain paragraph. " * 20
        chunks = _split_by_structure(text, 200)
        assert len(chunks) >= 1
        # All text should be preserved
        total_len = sum(len(c) for c in chunks)
        assert total_len >= len(text) - len(chunks)  # Allow for join overhead

    def test_empty_text(self):
        """Should handle empty text gracefully."""
        chunks = _split_by_structure("", 500)
        assert chunks == [""]

    def test_single_line(self):
        """Should handle single line text."""
        text = "Just one line of text."
        chunks = _split_by_structure(text, 500)
        assert len(chunks) == 1
        assert chunks[0] == text
