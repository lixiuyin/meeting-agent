"""Tests for audio chunk speaker-change splitting and forced speaker prefixes."""

from src.services.rag._indexer import (
    _build_chunk_with_speaker_alignment,
    _group_segments_by_boundaries,
)


def _make_segments(data: list[tuple[str, str, float, float]]) -> list[dict]:
    """Create segment dicts from (speaker, text, start, end) tuples."""
    return [{"speaker": sp, "text": txt, "start": s, "end": e} for sp, txt, s, e in data]


def _dummy_embeddings(count: int, dim: int = 4) -> list[list[float]]:
    return [[0.1 * (i + 1)] * dim for i in range(count)]


class TestSpeakerChangeSplitting:
    def test_speaker_change_creates_split(self) -> None:
        """With split_on_speaker_change=True, each speaker gets their own chunk."""
        segments = _make_segments(
            [
                ("Alice", "Hello", 0.0, 1.0),
                ("Bob", "Hi there", 1.0, 2.0),
                ("Alice", "How are you?", 2.0, 3.0),
                ("Bob", "Fine thanks", 3.0, 4.0),
            ]
        )
        embeddings = _dummy_embeddings(len(segments))
        chunks = _group_segments_by_boundaries(
            segments,
            embeddings,
            boundaries=set(),
            max_chunk_size=10000,
            include_speaker_in_content=False,
            split_on_speaker_change=True,
        )
        # 4 segments with alternating speakers = 4 chunks
        assert len(chunks) == 4
        assert chunks[0]["speaker"] == "Alice"
        assert chunks[1]["speaker"] == "Bob"
        assert chunks[2]["speaker"] == "Alice"
        assert chunks[3]["speaker"] == "Bob"

    def test_same_speaker_not_split(self) -> None:
        """With same speaker across segments, no speaker-change splits occur."""
        segments = _make_segments(
            [
                ("Alice", "First part", 0.0, 1.0),
                ("Alice", "Second part", 1.0, 2.0),
                ("Alice", "Third part", 2.0, 3.0),
            ]
        )
        embeddings = _dummy_embeddings(len(segments))
        chunks = _group_segments_by_boundaries(
            segments,
            embeddings,
            boundaries=set(),
            max_chunk_size=10000,
            include_speaker_in_content=False,
            split_on_speaker_change=True,
        )
        # All same speaker = 1 chunk
        assert len(chunks) == 1
        assert chunks[0]["speaker"] == "Alice"

    def test_speaker_change_disabled_merges(self) -> None:
        """With split_on_speaker_change=False, different speakers share chunks."""
        segments = _make_segments(
            [
                ("Alice", "Hello from Alice", 0.0, 1.0),
                ("Bob", "Hello from Bob", 1.0, 2.0),
            ]
        )
        embeddings = _dummy_embeddings(len(segments))
        chunks = _group_segments_by_boundaries(
            segments,
            embeddings,
            boundaries=set(),
            max_chunk_size=10000,
            include_speaker_in_content=False,
            split_on_speaker_change=False,
        )
        # Merged into one chunk
        assert len(chunks) == 1
        # Should have forced speaker prefixes since multi-speaker
        assert "Alice: Hello from Alice" in chunks[0]["text"]
        assert "Bob: Hello from Bob" in chunks[0]["text"]

    def test_no_speaker_field_graceful(self) -> None:
        """Segments without speaker field don't crash or cause unnecessary splits."""
        segments = [
            {"text": "No speaker here", "start": 0.0, "end": 1.0},
            {"text": "Still no speaker", "start": 1.0, "end": 2.0},
        ]
        embeddings = _dummy_embeddings(len(segments))
        chunks = _group_segments_by_boundaries(
            segments,
            embeddings,
            boundaries=set(),
            max_chunk_size=10000,
            include_speaker_in_content=False,
            split_on_speaker_change=True,
        )
        assert len(chunks) == 1

    def test_speaker_change_with_size_limit(self) -> None:
        """Speaker change splits correctly even when size limit would also split."""
        segments = _make_segments(
            [
                ("Alice", "A" * 50, 0.0, 1.0),
                ("Bob", "B" * 50, 1.0, 2.0),
            ]
        )
        embeddings = _dummy_embeddings(len(segments))
        chunks = _group_segments_by_boundaries(
            segments,
            embeddings,
            boundaries=set(),
            max_chunk_size=200,
            include_speaker_in_content=False,
            split_on_speaker_change=True,
        )
        assert len(chunks) == 2
        assert chunks[0]["speaker"] == "Alice"
        assert chunks[1]["speaker"] == "Bob"

    def test_semantic_boundary_still_works(self) -> None:
        """Semantic boundaries still cause splits even with same speaker."""
        segments = _make_segments(
            [
                ("Alice", "First topic", 0.0, 1.0),
                ("Alice", "Second topic", 1.0, 2.0),
            ]
        )
        embeddings = _dummy_embeddings(len(segments))
        chunks = _group_segments_by_boundaries(
            segments,
            embeddings,
            boundaries={1},
            max_chunk_size=10000,
            include_speaker_in_content=False,
            split_on_speaker_change=True,
        )
        assert len(chunks) == 2


class TestForcedSpeakerPrefix:
    def test_multi_speaker_forced_prefixes(self) -> None:
        """Multi-speaker chunk gets speaker prefixes even when include_speaker_in_content=False."""
        segments = _make_segments(
            [
                ("Alice", "Hello from Alice", 0.0, 1.0),
                ("Bob", "Hello from Bob", 1.0, 2.0),
            ]
        )
        chunk, _ = _build_chunk_with_speaker_alignment(
            segments,
            [0, 1],
            _dummy_embeddings(2),
            None,
            include_speaker_in_content=False,
        )
        assert "Alice: Hello from Alice" in chunk["text"]
        assert "Bob: Hello from Bob" in chunk["text"]

    def test_single_speaker_no_forced_prefix(self) -> None:
        """Single-speaker chunk respects include_speaker_in_content=False."""
        segments = _make_segments(
            [
                ("Alice", "Hello from Alice", 0.0, 1.0),
                ("Alice", "More from Alice", 1.0, 2.0),
            ]
        )
        chunk, _ = _build_chunk_with_speaker_alignment(
            segments,
            [0, 1],
            _dummy_embeddings(2),
            None,
            include_speaker_in_content=False,
        )
        # No forced prefix for single-speaker chunk
        assert "Alice: " not in chunk["text"]
        assert "Hello from Alice" in chunk["text"]

    def test_single_speaker_with_include_flag(self) -> None:
        """Single-speaker chunk gets prefixes when include_speaker_in_content=True."""
        segments = _make_segments(
            [
                ("Alice", "Hello from Alice", 0.0, 1.0),
                ("Alice", "More from Alice", 1.0, 2.0),
            ]
        )
        chunk, _ = _build_chunk_with_speaker_alignment(
            segments,
            [0, 1],
            _dummy_embeddings(2),
            None,
            include_speaker_in_content=True,
        )
        assert "Alice: Hello from Alice" in chunk["text"]
        assert "Alice: More from Alice" in chunk["text"]

    def test_speaker_metadata_is_first_speaker(self) -> None:
        """The chunk's speaker metadata field is the first segment's speaker."""
        segments = _make_segments(
            [
                ("Alice", "Hello", 0.0, 1.0),
                ("Bob", "Hi", 1.0, 2.0),
            ]
        )
        chunk, _ = _build_chunk_with_speaker_alignment(
            segments,
            [0, 1],
            _dummy_embeddings(2),
            None,
            include_speaker_in_content=False,
        )
        assert chunk["speaker"] == "Alice"
