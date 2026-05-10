"""Tests for history sanitization and citation-marker stripping (S1-B)."""

from langchain_core.messages import AIMessage, HumanMessage

from src.services.chain._steps_session import (
    _strip_citation_markers,
    sanitize_history_messages,
)


def test_strip_citation_markers_removes_single_marker():
    assert _strip_citation_markers("The deadline is March 15 [1].") == "The deadline is March 15."


def test_strip_citation_markers_removes_grouped_markers():
    out = _strip_citation_markers("Topics covered [1][2] include X [3].")
    assert "[1]" not in out and "[2]" not in out and "[3]" not in out
    assert out == "Topics covered include X."


def test_strip_citation_markers_preserves_urls_with_brackets():
    url = "See https://example.com/path/[docs] for details."
    # Regex anchors on \w boundary, so bracketed text after a slash stays intact.
    assert _strip_citation_markers(url) == url


def test_strip_citation_markers_noop_when_no_markers():
    text = "No citations here, just plain text."
    assert _strip_citation_markers(text) is text or _strip_citation_markers(text) == text


def test_strip_citation_markers_on_multiline():
    text = "Line one [1].\n\nLine two [2] and [3]."
    out = _strip_citation_markers(text)
    assert "[" not in out
    assert "Line one." in out
    assert "Line two and." in out


def test_sanitize_history_strips_markers_from_ai_turns_only(monkeypatch):
    messages = [
        HumanMessage(content="What is [1] the plan?"),  # human: keep as-is
        AIMessage(content="The plan is X [1], with Y [2]."),
        HumanMessage(content="Follow up"),
        AIMessage(content="See details [3][4]."),
    ]
    out = sanitize_history_messages(messages, max_tokens=10_000)

    # Human messages: untouched
    assert out[0].content == "What is [1] the plan?"
    assert out[2].content == "Follow up"
    # AI messages: markers gone
    assert "[1]" not in out[1].content
    assert out[1].content.startswith("The plan is X")
    assert "[3]" not in out[3].content and "[4]" not in out[3].content


def test_sanitize_history_drops_ai_message_that_becomes_empty():
    messages = [
        AIMessage(content="[1] [2] [3]"),  # entirely citations
        HumanMessage(content="next"),
    ]
    out = sanitize_history_messages(messages, max_tokens=10_000)
    assert len(out) == 1
    assert out[0].content == "next"
