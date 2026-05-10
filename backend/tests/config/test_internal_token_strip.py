"""Tests for internal token stripping in _steps_generate.py."""

from src.services.chain._steps_generate import _strip_internal_tokens


def test_strips_lowercase_tokens():
    assert _strip_internal_tokens("[meeting_summaries] some text") == "some text"


def test_strips_capitalized_meeting_summaries():
    assert _strip_internal_tokens("[Meeting Summaries] some text") == "some text"


def test_strips_meeting_summary_singular():
    assert _strip_internal_tokens("[Meeting Summary] some text") == "some text"


def test_strips_file_summaries_capitalized():
    assert _strip_internal_tokens("[File Summaries] text") == "text"


def test_strips_file_summary_singular():
    assert _strip_internal_tokens("[File Summary] text") == "text"


def test_strips_user_memory_capitalized():
    assert _strip_internal_tokens("[User Memory] text") == "text"


def test_strips_web_search_capitalized():
    assert _strip_internal_tokens("[Web Search] text") == "text"


def test_strips_image_tokens():
    assert _strip_internal_tokens("[Image #1] text") == "text"
    assert _strip_internal_tokens("[Image #42] more") == "more"
    assert _strip_internal_tokens("[Image] bare") == "bare"


def test_strips_file_colon_tokens():
    assert _strip_internal_tokens("[file:11] text") == "text"


def test_strips_mixed_tokens():
    result = _strip_internal_tokens(
        "[Meeting Summaries] The project [File Summary] is on track [file:3]. [Image #2]"
    )
    assert "[Meeting Summaries]" not in result
    assert "[File Summary]" not in result
    assert "[file:3]" not in result
    assert "[Image #2]" not in result
    assert "The project" in result
    assert "is on track" in result


def test_preserves_numeric_citations():
    assert _strip_internal_tokens("See [3] for details") == "See [3] for details"


def test_collapses_extra_spaces():
    result = _strip_internal_tokens("hello  [Meeting Summaries]   world")
    assert result == "hello world"
