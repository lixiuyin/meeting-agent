"""Tests for speaker delimiter handling in speakers_in_chunk metadata."""

from src.services.rag._indexer import (
    _SPEAKER_DELIMITER,
    _scrub_segment_text,
    display_speakers,
    split_speakers,
)


class TestSplitSpeakers:
    def test_handles_unit_separator(self) -> None:
        result = split_speakers(f"Alice{_SPEAKER_DELIMITER}Bob{_SPEAKER_DELIMITER}Charlie")
        assert result == ["Alice", "Bob", "Charlie"]

    def test_handles_legacy_comma(self) -> None:
        result = split_speakers("Alice,Bob,Charlie")
        assert result == ["Alice", "Bob", "Charlie"]

    def test_handles_mixed_delimiters(self) -> None:
        raw = f"Alice{_SPEAKER_DELIMITER}Bob,Charlie"
        result = split_speakers(raw)
        assert result == ["Alice", "Bob", "Charlie"]

    def test_handles_empty(self) -> None:
        assert split_speakers("") == []

    def test_strips_whitespace(self) -> None:
        result = split_speakers(" Alice , Bob ")
        assert result == ["Alice", "Bob"]

    def test_single_speaker(self) -> None:
        assert split_speakers("Alice") == ["Alice"]

    def test_filters_empty_parts(self) -> None:
        result = split_speakers(f"Alice{_SPEAKER_DELIMITER}{_SPEAKER_DELIMITER}Bob")
        assert result == ["Alice", "Bob"]


class TestDisplaySpeakers:
    def test_formats_unit_separator(self) -> None:
        raw = f"Alice{_SPEAKER_DELIMITER}Bob{_SPEAKER_DELIMITER}Charlie"
        assert display_speakers(raw) == "Alice, Bob, Charlie"

    def test_single_speaker_unchanged(self) -> None:
        assert display_speakers("Alice") == "Alice"

    def test_empty_string_unchanged(self) -> None:
        assert display_speakers("") == ""


class TestDelimiterConstant:
    def test_delimiter_is_unit_separator(self) -> None:
        assert _SPEAKER_DELIMITER == "\x1f"

    def test_join_produces_correct_format(self) -> None:
        speakers = ["Alice", "Bob"]
        joined = _SPEAKER_DELIMITER.join(speakers)
        assert joined == "Alice\x1fBob"
        assert "," not in joined


class TestScrubSegmentText:
    def test_strips_leading_marker(self) -> None:
        assert _scrub_segment_text("[Speaker] We found that...") == "We found that..."

    def test_strips_inline_marker(self) -> None:
        assert _scrub_segment_text("hello [Speaker] world") == "hello world"

    def test_collapses_extra_whitespace(self) -> None:
        assert _scrub_segment_text("a  [Speaker]  b") == "a b"

    def test_preserves_clean_text(self) -> None:
        assert _scrub_segment_text("plain text") == "plain text"

    def test_empty_input(self) -> None:
        assert _scrub_segment_text("") == ""
