"""Tests for build_context summary preservation through truncation.

Covers the 4 bugs fixed in _steps_generate.py:
- Bug 1: Truncation drops summaries from rebuilt context
- Bug 2: Synthetic summary docs get popped during truncation
- Bug 3: Meeting-scoped queries get truncated/incomplete file summaries
- Bug 4: file_meeting_id_map not built from DB
"""

import pytest

from src.core.config import settings
from src.core.database import get_write_connection
from src.services.chain._context import PipelineContext
from src.services.chain._formatting import _extract_sources
from src.services.chain._steps_generate import (
    _load_file_summaries,
    _load_meeting_summaries_for_context,
    build_context,
)


def _seed_meeting_with_files(meeting_id: int, title: str = "Test Meeting"):
    """Seed a meeting with two files + summary into the shared test database."""
    with get_write_connection() as conn:
        conn.execute(
            "INSERT INTO meetings (id, title, status, summary_status) VALUES (?, ?, 'ready', 'ready')",
            (meeting_id, title),
        )
        conn.execute(
            "INSERT INTO meeting_files (id, meeting_id, file_name, file_path, file_type, status, summary) "
            "VALUES (?, ?, ?, ?, 'video', 'ready', ?)",
            (
                meeting_id * 100 + 1,
                meeting_id,
                f"file_{meeting_id}_a.mp4",
                f"/fake/file_{meeting_id}_a.mp4",
                f"Summary of file A in meeting {meeting_id}",
            ),
        )
        conn.execute(
            "INSERT INTO meeting_files (id, meeting_id, file_name, file_path, file_type, status, summary) "
            "VALUES (?, ?, ?, ?, 'pdf', 'ready', ?)",
            (
                meeting_id * 100 + 2,
                meeting_id,
                f"file_{meeting_id}_b.pdf",
                f"/fake/file_{meeting_id}_b.pdf",
                f"Summary of file B in meeting {meeting_id}",
            ),
        )
        conn.execute(
            "INSERT INTO meeting_summaries (meeting_id, summary) VALUES (?, ?)",
            (meeting_id, f"Full meeting summary for meeting {meeting_id}"),
        )
        conn.commit()


def _make_chunk_doc(
    meeting_id: int,
    file_id: int,
    file_name: str,
    chunk_index: int = 0,
    content: str = "",
) -> dict:
    return {
        "content": content or f"Chunk {chunk_index} from file {file_id} in meeting {meeting_id}",
        "metadata": {
            "meeting_id": meeting_id,
            "file_id": file_id,
            "file_name": file_name,
            "chunk_index": chunk_index,
        },
        "score": 0.85,
    }


@pytest.fixture(autouse=True)
def _seed_data():
    """Seed test data before each test (auto-clean happens in conftest)."""
    _seed_meeting_with_files(1, "Alpha Meeting")
    _seed_meeting_with_files(2, "Beta Meeting")


class TestFileSummaryLoaderReturnType:
    """Change A: loaders return tuple[str, list[dict]] without mutating ctx.docs."""

    @pytest.mark.unit
    def test_returns_tuple(self):
        """_load_file_summaries returns (str, list[dict])."""
        ctx = PipelineContext(question="summarize the meeting")
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]
        result = _load_file_summaries(ctx)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], list)

    @pytest.mark.unit
    def test_does_not_mutate_ctx_docs(self):
        """_load_file_summaries does not mutate ctx.docs."""
        ctx = PipelineContext(question="summarize the meeting")
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]
        docs_before = list(ctx.docs)
        _load_file_summaries(ctx)
        assert ctx.docs == docs_before

    @pytest.mark.unit
    def test_meeting_loader_returns_tuple(self):
        """_load_meeting_summaries_for_context returns (str, list[dict])."""
        ctx = PipelineContext(question="summarize the meeting")
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]
        result = _load_meeting_summaries_for_context(ctx)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], list)

    @pytest.mark.unit
    def test_meeting_loader_does_not_mutate_ctx_docs(self):
        """_load_meeting_summaries_for_context does not mutate ctx.docs."""
        ctx = PipelineContext(question="summarize the meeting")
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]
        docs_before = list(ctx.docs)
        _load_meeting_summaries_for_context(ctx)
        assert ctx.docs == docs_before


class TestMeetingScopedFileSummaries:
    """Change B: meeting-scoped queries get full file summaries for all files."""

    @pytest.mark.unit
    def test_meeting_scoped_loads_all_files(self):
        """ctx.meeting_ids loads ALL files in those meetings, not just funnel results."""
        ctx = PipelineContext(question="summarize", meeting_ids=[1])
        ctx.docs = []
        ctx.scope_file_ids = []

        text, docs = _load_file_summaries(ctx)
        assert len(docs) >= 2
        assert any("file_1_a" in str(d.get("metadata", {}).get("file_name", "")) for d in docs)
        assert any("file_1_b" in str(d.get("metadata", {}).get("file_name", "")) for d in docs)

    @pytest.mark.unit
    def test_meeting_scoped_full_summary_no_truncation(self):
        """Meeting-scoped queries inject full summary without truncation."""
        ctx = PipelineContext(question="summarize", meeting_ids=[1])
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]
        ctx.scope_file_ids = [101]

        text, docs = _load_file_summaries(ctx)
        assert "Summary of file A in meeting 1" in text

    @pytest.mark.unit
    def test_file_scoped_no_truncation(self):
        """File-scoped queries inject full summary without truncation."""
        ctx = PipelineContext(question="summarize", file_ids=[101])
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]

        text, docs = _load_file_summaries(ctx)
        assert "Summary of file A in meeting 1" in text

    @pytest.mark.unit
    def test_unscoped_still_truncates(self):
        """Unscoped (no meeting_ids, no file_ids) still applies truncation."""
        long_summary = "x" * (settings.FILE_SUMMARY_CONTEXT_CHARS + 500)
        with get_write_connection() as conn:
            conn.execute("INSERT INTO meetings (id, title, status) VALUES (3, 'Long', 'ready')")
            conn.execute(
                "INSERT INTO meeting_files (id, meeting_id, file_name, file_path, file_type, status, summary) "
                "VALUES (301, 3, 'long.mp4', '/fake/long.mp4', 'video', 'ready', ?)",
                (long_summary,),
            )
            conn.commit()

        ctx = PipelineContext(question="summarize everything")
        ctx.docs = [_make_chunk_doc(3, 301, "long.mp4")]
        ctx.scope_file_ids = [301]

        text, docs = _load_file_summaries(ctx)
        assert len(text) < len(long_summary)

    @pytest.mark.unit
    def test_synthetic_docs_have_correct_meeting_id(self):
        """Synthetic docs get correct meeting_id even for files with no chunks."""
        ctx = PipelineContext(question="summarize", meeting_ids=[1], file_ids=[101])
        ctx.docs = []

        text, docs = _load_file_summaries(ctx)
        assert len(docs) > 0
        for doc in docs:
            meta = doc.get("metadata", {})
            assert meta.get("meeting_id") is not None


class TestMeetingSummariesContext:
    """Change C: meeting summary loader includes selected meetings."""

    @pytest.mark.unit
    def test_selected_meeting_included_when_no_chunks(self):
        """Meeting summary loaded for ctx.meeting_ids even when zero chunks."""
        ctx = PipelineContext(question="summarize meeting 2", meeting_ids=[2])
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]

        text, docs = _load_meeting_summaries_for_context(ctx)
        assert "Full meeting summary for meeting 2" in text
        doc_meeting_ids = {d.get("metadata", {}).get("meeting_id") for d in docs}
        assert 2 in doc_meeting_ids

    @pytest.mark.unit
    def test_small_scope_full_summary(self):
        """<= 2 meetings: full summary injected without truncation."""
        ctx = PipelineContext(question="summarize all")
        ctx.docs = [
            _make_chunk_doc(1, 101, "file_1_a.mp4"),
            _make_chunk_doc(2, 201, "file_2_a.mp4"),
        ]

        text, docs = _load_meeting_summaries_for_context(ctx)
        assert "Full meeting summary for meeting 1" in text
        assert "Full meeting summary for meeting 2" in text

    @pytest.mark.unit
    def test_date_in_heading(self):
        """Meeting heading includes title when available."""
        ctx = PipelineContext(question="summarize meeting 1")
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]

        text, docs = _load_meeting_summaries_for_context(ctx)
        assert "Alpha Meeting" in text


class TestBuildContextTruncation:
    """Change D: build_context preserves summaries through truncation."""

    @pytest.mark.unit
    def test_summaries_in_combined_context(self):
        """combined_context includes file_summaries and meeting_summaries."""
        ctx = PipelineContext(question="summarize meeting 1")
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]

        build_context(ctx)

        # Summary docs should be in ctx.docs (appended after truncation).
        summary_docs = [
            d
            for d in ctx.docs
            if (d.get("metadata") or {}).get("source_kind") in ("file_summary", "meeting_summary")
        ]
        assert len(summary_docs) > 0, f"Expected summary docs in ctx.docs, got {len(summary_docs)}"

    @pytest.mark.unit
    def test_summary_docs_in_extract_sources(self):
        """_extract_sources includes file_summary and meeting_summary entries."""
        ctx = PipelineContext(question="summarize meeting 1")
        ctx.docs = [_make_chunk_doc(1, 101, "file_1_a.mp4")]

        file_text, file_docs = _load_file_summaries(ctx)
        meet_text, meet_docs = _load_meeting_summaries_for_context(ctx)
        all_docs = list(ctx.docs) + file_docs + meet_docs

        sources = _extract_sources(all_docs)
        kinds = {s.get("source_kind") for s in sources}
        assert "file_summary" in kinds
        assert "meeting_summary" in kinds
