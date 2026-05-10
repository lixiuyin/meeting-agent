"""Tests for web search service - multiple provider support.

Tests cover:
- SearchResult dataclass
- Result formatting
- HTTP client management
"""

import os
import tempfile
from pathlib import Path

import pytest

# Set up test environment
os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from src.core import constants as constants_module

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"

from src.services import search  # noqa: E402
from src.services.search import SearchResult, format_search_results  # noqa: E402


class TestSearchResultDataclass:
    """Test SearchResult data class."""

    def test_search_result_creation(self):
        """Should create SearchResult with all fields."""
        result = SearchResult(
            title="Test Title",
            url="https://example.com",
            snippet="Test snippet",
            source="duckduckgo",
        )
        assert result.title == "Test Title"
        assert result.url == "https://example.com"
        assert result.snippet == "Test snippet"
        assert result.source == "duckduckgo"


class TestFormatSearchResults:
    """Test search result formatting."""

    def test_format_empty_results(self):
        """Should handle empty results."""
        formatted = format_search_results([])
        assert formatted == ""

    def test_format_single_result(self):
        """Should format single result."""
        results = [
            SearchResult(
                title="Test",
                url="https://example.com",
                snippet="A test result",
                source="duckduckgo",
            )
        ]
        formatted = format_search_results(results)
        assert "Test" in formatted
        assert "https://example.com" in formatted
        assert "A test result" in formatted

    def test_format_multiple_results(self):
        """Should format multiple results."""
        results = [
            SearchResult(
                title=f"Result {i}",
                url=f"https://example{i}.com",
                snippet=f"Snippet {i}",
                source="duckduckgo",
            )
            for i in range(3)
        ]
        formatted = format_search_results(results)
        assert "Result 0" in formatted
        assert "Result 1" in formatted
        assert "Result 2" in formatted
        # Check numbering format
        assert formatted.count("[") >= 3

    def test_format_escapes_special_chars(self):
        """Should handle results with special characters."""
        results = [
            SearchResult(
                title="Title with <special> chars",
                url="https://example.com?foo=bar&baz=qux",
                snippet="Snippet with unicode: hello world 🎉",
                source="test",
            )
        ]
        formatted = format_search_results(results)
        assert "Title" in formatted
        assert "example.com" in formatted


class TestHTTPClient:
    """Test HTTP client management."""

    def test_get_http_client_singleton(self):
        """Should return same client instance."""
        client1 = search.get_http_client()
        client2 = search.get_http_client()
        assert client1 is client2

    @pytest.mark.asyncio
    async def test_close_http_client(self):
        """Should close client and reset singleton."""
        client = search.get_http_client()
        assert client is not None

        await search.close_http_client()
        new_client = search.get_http_client()
        assert new_client is not client


class TestSearchImports:
    """Test that search module exports are available."""

    def test_search_function_exists(self):
        """Should have search_duckduckgo function."""
        assert hasattr(search, "search_duckduckgo")
        assert hasattr(search, "search_serpapi")
        assert hasattr(search, "search_tavily")
        assert hasattr(search, "search_bing")
        assert hasattr(search, "search_exa")
        assert hasattr(search, "search_with_fallback")

    def test_search_result_exists(self):
        """Should export SearchResult."""
        assert hasattr(search, "SearchResult")

    def test_format_function_exists(self):
        """Should export format_search_results."""
        assert hasattr(search, "format_search_results")
