"""Tests for per-file summary input truncation (P0-1)."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.chain._per_file_summary import (
    _render_speaker_timeline,
    _truncate_to_token_budget,
    generate_per_file_summary,
)


def test_truncate_returns_text_when_under_budget():
    text = "short content"
    assert _truncate_to_token_budget(text, max_tokens=1000, model="gpt-4o-mini") == text


def test_truncate_returns_text_when_budget_non_positive():
    text = "any content"
    assert _truncate_to_token_budget(text, max_tokens=0, model="gpt-4o-mini") == text


def test_truncate_shrinks_long_text_and_appends_marker():
    text = "word " * 20000
    out = _truncate_to_token_budget(text, max_tokens=200, model="gpt-4o-mini")
    assert out.endswith("[truncated]")
    assert len(out) < len(text)


@pytest.mark.anyio
async def test_generate_per_file_summary_truncates_long_input(monkeypatch):
    """End-to-end: huge transcript must not flow verbatim into the LLM prompt."""
    monkeypatch.setattr(
        "src.services.chain._per_file_summary.settings.PER_FILE_SUMMARY_INPUT_MAX_TOKENS",
        100,
    )

    captured: dict[str, str] = {}

    class _FakeLLM:
        async def ainvoke(self, prompt: str):
            captured["prompt"] = prompt
            resp = AsyncMock()
            resp.content = "- first key discussion point\n- second key decision made"
            return resp

    with patch("src.services.chain._per_file_summary.llm_service.get_llm", return_value=_FakeLLM()):
        huge = "word " * 10000
        summary, points = await generate_per_file_summary(
            file_type="pdf", file_name="big.pdf", text=huge
        )

    assert "[truncated]" in captured["prompt"]
    assert len(captured["prompt"]) < len(huge)
    assert summary.startswith("- first")
    assert points  # key points parsed from summary


@pytest.mark.anyio
async def test_generate_per_file_summary_keeps_short_input_intact(monkeypatch):
    monkeypatch.setattr(
        "src.services.chain._per_file_summary.settings.PER_FILE_SUMMARY_INPUT_MAX_TOKENS",
        8000,
    )

    captured: dict[str, str] = {}

    class _FakeLLM:
        async def ainvoke(self, prompt: str):
            captured["prompt"] = prompt
            resp = AsyncMock()
            resp.content = "ok"
            return resp

    with patch("src.services.chain._per_file_summary.llm_service.get_llm", return_value=_FakeLLM()):
        await generate_per_file_summary(file_type="pdf", file_name="x.pdf", text="hello world")

    assert "[truncated]" not in captured["prompt"]
    assert "hello world" in captured["prompt"]


# --- Speaker timeline rendering ---


def test_render_speaker_timeline_empty():
    assert _render_speaker_timeline([]) == ""


def test_render_speaker_timeline_groups_by_speaker():
    segments = [
        {"start": 0.0, "end": 5.0, "speaker": "Alice", "text": "Hello"},
        {"start": 5.0, "end": 10.0, "speaker": "Bob", "text": "Hi there"},
        {"start": 10.0, "end": 15.0, "speaker": "Alice", "text": "Welcome"},
    ]
    result = _render_speaker_timeline(segments)
    assert "### Speaker Timeline" in result
    assert "**Alice**:" in result
    assert "**Bob**:" in result
    assert "[00:00:00] Alice: Hello" in result
    assert "[00:00:05] Bob: Hi there" in result
    assert "[00:00:10] Alice: Welcome" in result


def test_render_speaker_timeline_handles_missing_speaker():
    segments = [
        {"start": 0.0, "end": 5.0, "text": "Nobody knows"},
    ]
    result = _render_speaker_timeline(segments)
    assert "**Unknown**:" in result


def test_render_speaker_timeline_skips_empty_text():
    segments = [
        {"start": 0.0, "end": 5.0, "speaker": "Alice", "text": ""},
        {"start": 5.0, "end": 10.0, "speaker": "Alice", "text": "  "},
        {"start": 10.0, "end": 15.0, "speaker": "Alice", "text": "Real content"},
    ]
    result = _render_speaker_timeline(segments)
    assert result.count("Alice:") == 1  # Only the header + one real entry
    assert "Real content" in result


@pytest.mark.anyio
async def test_generate_per_file_summary_includes_timeline_for_video(monkeypatch):
    monkeypatch.setattr(
        "src.services.chain._per_file_summary.settings.PER_FILE_SUMMARY_INPUT_MAX_TOKENS",
        8000,
    )

    captured: dict[str, str] = {}

    class _FakeLLM:
        async def ainvoke(self, prompt: str):
            captured["prompt"] = prompt
            resp = AsyncMock()
            resp.content = "summary"
            return resp

    segments = [
        {"start": 0.0, "end": 5.0, "speaker": "Alice", "text": "Hello"},
        {"start": 5.0, "end": 10.0, "speaker": "Bob", "text": "World"},
    ]

    with patch("src.services.chain._per_file_summary.llm_service.get_llm", return_value=_FakeLLM()):
        await generate_per_file_summary(
            file_type="video",
            file_name="meeting.mp4",
            text="transcript text",
            segments=segments,
        )

    assert "Speaker Timeline" in captured["prompt"]
    assert "Alice" in captured["prompt"]
    assert "Bob" in captured["prompt"]


@pytest.mark.anyio
async def test_generate_per_file_summary_omits_timeline_for_pdf(monkeypatch):
    monkeypatch.setattr(
        "src.services.chain._per_file_summary.settings.PER_FILE_SUMMARY_INPUT_MAX_TOKENS",
        8000,
    )

    captured: dict[str, str] = {}

    class _FakeLLM:
        async def ainvoke(self, prompt: str):
            captured["prompt"] = prompt
            resp = AsyncMock()
            resp.content = "summary"
            return resp

    segments = [{"start": 0.0, "end": 5.0, "speaker": "Alice", "text": "Hello"}]

    with patch("src.services.chain._per_file_summary.llm_service.get_llm", return_value=_FakeLLM()):
        await generate_per_file_summary(
            file_type="pdf",
            file_name="doc.pdf",
            text="document text",
            segments=segments,
        )

    assert "Speaker Timeline" not in captured["prompt"]
