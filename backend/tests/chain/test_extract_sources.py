"""Tests for _extract_sources dedup and source_kind preservation."""

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

from src.services.chain._formatting import (  # noqa: E402
    _canonical_citation_docs,
    _extract_sources,
    _format_docs,
    _infer_source_kind,
)


class TestInferSourceKind:
    def test_meeting_summary_pass_through(self):
        meta = {"source_kind": "meeting_summary", "meeting_id": 5}
        assert _infer_source_kind(meta) == "meeting_summary"

    def test_file_summary_pass_through(self):
        meta = {"source_kind": "file_summary", "meeting_id": 5, "file_id": 10}
        assert _infer_source_kind(meta) == "file_summary"

    def test_timestamp_from_field(self):
        meta = {"timestamp_start": 10, "timestamp_end": 20, "file_type": "video"}
        assert _infer_source_kind(meta) == "timestamp"

    def test_text_fallback(self):
        meta = {"meeting_id": 1, "file_id": 2}
        assert _infer_source_kind(meta) == "text"

    def test_original_format_pptx_returns_slide(self):
        """PPTX converted to PDF should still cite as 'slide'."""
        meta = {"original_format": "pptx", "file_type": "pdf", "page_number": 3}
        assert _infer_source_kind(meta) == "slide"

    def test_original_format_ppt_returns_slide(self):
        meta = {"original_format": "ppt", "file_type": "pdf", "page_number": 1}
        assert _infer_source_kind(meta) == "slide"

    def test_no_original_format_pdf_returns_page(self):
        """PDF without original_format should return 'page', not 'slide'."""
        meta = {"file_type": "pdf", "page_number": 3}
        assert _infer_source_kind(meta) == "page"


class TestExtractSourcesDedup:
    def _chunk_doc(self, meeting_id=1, file_id=10, chunk_index=0, **extra):
        return {
            "content": "chunk content",
            "metadata": {
                "meeting_id": meeting_id,
                "file_id": file_id,
                "chunk_index": chunk_index,
                "file_name": "test.pdf",
                "file_type": "pdf",
                **extra,
            },
            "score": 0.85,
        }

    def _meeting_summary_doc(self, meeting_id=1):
        return {
            "content": "meeting summary text",
            "metadata": {
                "meeting_id": meeting_id,
                "title": "Test Meeting",
                "source_kind": "meeting_summary",
                "summary_kind": "meeting",
                "chunk_index": None,
            },
            "score": 0.0,
        }

    def _file_summary_doc(self, meeting_id=1, file_id=10):
        return {
            "content": "file summary text",
            "metadata": {
                "meeting_id": meeting_id,
                "file_id": file_id,
                "file_name": "test.pdf",
                "source_kind": "file_summary",
                "chunk_index": None,
                "page_number": 1,
            },
            "score": 0.0,
        }

    def test_meeting_summary_source_kind_preserved(self):
        docs = [self._meeting_summary_doc(meeting_id=5)]
        sources = _extract_sources(docs)
        assert len(sources) == 1
        assert sources[0]["source_kind"] == "meeting_summary"

    def test_file_summary_source_kind_preserved(self):
        docs = [self._file_summary_doc(meeting_id=5, file_id=10)]
        sources = _extract_sources(docs)
        assert len(sources) == 1
        assert sources[0]["source_kind"] == "file_summary"

    def test_file_summary_has_page_number(self):
        docs = [self._file_summary_doc(meeting_id=5, file_id=10)]
        sources = _extract_sources(docs)
        assert sources[0]["page_number"] == 1

    def test_chunk_and_summary_no_dedup_collision(self):
        """A chunk doc and a meeting summary doc for the same meeting must
        both survive dedup because the dedup key is namespaced by source_kind."""
        docs = [
            self._chunk_doc(meeting_id=5, file_id=10, chunk_index=0),
            self._meeting_summary_doc(meeting_id=5),
        ]
        sources = _extract_sources(docs)
        assert len(sources) == 2
        kinds = {s["source_kind"] for s in sources}
        assert kinds == {"text", "meeting_summary"}

    def test_file_summary_and_chunk_no_dedup_collision(self):
        """A chunk doc and a file summary doc for the same file must both survive."""
        docs = [
            self._chunk_doc(meeting_id=5, file_id=10, chunk_index=0),
            self._file_summary_doc(meeting_id=5, file_id=10),
        ]
        sources = _extract_sources(docs)
        assert len(sources) == 2
        kinds = {s["source_kind"] for s in sources}
        assert kinds == {"text", "file_summary"}

    def test_duplicate_chunks_deduped(self):
        docs = [
            self._chunk_doc(meeting_id=1, file_id=10, chunk_index=0),
            self._chunk_doc(meeting_id=1, file_id=10, chunk_index=0),
        ]
        sources = _extract_sources(docs)
        assert len(sources) == 1

    def test_no_meeting_id_skipped(self):
        docs = [{"content": "orphan", "metadata": {"file_id": 1}, "score": 0.5}]
        sources = _extract_sources(docs)
        assert len(sources) == 0

    def test_max_sources_limit(self):
        docs = [self._chunk_doc(meeting_id=i, file_id=i * 10, chunk_index=0) for i in range(1, 6)]
        sources = _extract_sources(docs, max_sources=3)
        assert len(sources) == 3


class TestCanonicalCitationDocsAlignment:
    """Verify _canonical_citation_docs keeps _format_docs [N] aligned with
    _extract_sources sources[N-1]."""

    def _make_doc(
        self,
        meeting_id: int | None = 1,
        file_id: int | None = 1,
        chunk_index: int | None = 0,
        source_kind: str | None = None,
        file_type: str = "pdf",
        page_number: int | None = None,
        content: str = "chunk content",
    ) -> dict:
        meta: dict = {
            "meeting_id": meeting_id,
            "file_id": file_id,
            "chunk_index": chunk_index,
            "file_type": file_type,
            "file_name": f"test.{file_type}",
        }
        if source_kind is not None:
            meta["source_kind"] = source_kind
        if page_number is not None:
            meta["page_number"] = page_number
        return {"content": content, "metadata": meta, "score": 0.9}

    def test_format_docs_aligned_with_extract_sources(self):
        """LLM [N] in _format_docs must point to the same chunk as sources[N-1]."""
        docs = [
            # duplicate chunk (BM25 + vector both returned it)
            self._make_doc(meeting_id=1, file_id=1, chunk_index=5, source_kind="page"),
            self._make_doc(meeting_id=1, file_id=1, chunk_index=5, source_kind="page"),
            self._make_doc(meeting_id=1, file_id=2, chunk_index=3, source_kind="page"),
            # doc with meeting_id=None (would be filtered by _extract_sources)
            self._make_doc(meeting_id=None, file_id=None, chunk_index=None),
            self._make_doc(meeting_id=2, file_id=1, chunk_index=7, source_kind="timestamp"),
        ]
        canonical = _canonical_citation_docs(docs)
        sources = _extract_sources(canonical)
        formatted = _format_docs(canonical)

        # For every [N] header in formatted output, sources[N-1] must reference
        # the same (meeting_id, file_id, chunk_index) tuple.
        for n, src in enumerate(sources, 1):
            assert f"[{n}]" in formatted
            canonical_meta = canonical[n - 1]["metadata"]
            assert (
                src["meeting_id"],
                src["file_id"],
                src["chunk_index"],
            ) == (
                canonical_meta["meeting_id"],
                canonical_meta["file_id"],
                canonical_meta["chunk_index"],
            )

    def test_dedup_removes_duplicates(self):
        docs = [
            self._make_doc(meeting_id=1, file_id=1, chunk_index=0),
            self._make_doc(meeting_id=1, file_id=1, chunk_index=0),
            self._make_doc(meeting_id=1, file_id=2, chunk_index=0),
        ]
        canonical = _canonical_citation_docs(docs)
        assert len(canonical) == 2

    def test_filters_no_meeting_id(self):
        docs = [
            self._make_doc(meeting_id=None, file_id=None, chunk_index=None),
            self._make_doc(meeting_id=1, file_id=1, chunk_index=0),
        ]
        canonical = _canonical_citation_docs(docs)
        assert len(canonical) == 1
        assert canonical[0]["metadata"]["meeting_id"] == 1

    def test_preserves_order(self):
        docs = [
            self._make_doc(meeting_id=3, file_id=1, chunk_index=0, content="third"),
            self._make_doc(meeting_id=1, file_id=1, chunk_index=0, content="first"),
            self._make_doc(meeting_id=2, file_id=1, chunk_index=0, content="second"),
        ]
        canonical = _canonical_citation_docs(docs)
        assert [d["content"] for d in canonical] == ["third", "first", "second"]

    def test_empty_input(self):
        assert _canonical_citation_docs([]) == []

    def test_summary_docs_after_canonical_stay_aligned(self):
        """Summary docs appended after canonicalization get predictable indexes."""
        chunk_docs = [
            self._make_doc(meeting_id=1, file_id=1, chunk_index=0),
            self._make_doc(meeting_id=1, file_id=1, chunk_index=0),  # duplicate
        ]
        canonical = _canonical_citation_docs(chunk_docs)
        summary_doc = {
            "content": "summary text",
            "metadata": {
                "meeting_id": 1,
                "file_id": 1,
                "source_kind": "file_summary",
                "chunk_index": None,
            },
            "score": 0.5,
        }
        # Simulate what build_context does: append after canonicalization
        final_docs = [*canonical, summary_doc]
        sources = _extract_sources(final_docs)
        # 1 unique chunk + 1 file summary = 2 sources
        assert len(sources) == 2
        assert sources[0]["source_kind"] == "text"
        assert sources[1]["source_kind"] == "file_summary"

    def test_summary_citations_aligned_in_format_docs(self):
        """Summary docs get [N] numbers and stay aligned with sources."""
        chunk_docs = [
            self._make_doc(meeting_id=1, file_id=1, chunk_index=0),
            self._make_doc(meeting_id=1, file_id=2, chunk_index=0),
        ]
        canonical = _canonical_citation_docs(chunk_docs)
        file_summary = {
            "content": "file summary text",
            "metadata": {
                "meeting_id": 1,
                "file_id": 1,
                "file_name": "test.pdf",
                "source_kind": "file_summary",
                "chunk_index": None,
                "page_number": 1,
            },
            "score": 0.5,
        }
        meeting_summary = {
            "content": "meeting summary text",
            "metadata": {
                "meeting_id": 1,
                "title": "Test Meeting",
                "meeting_title": "Test Meeting",
                "source_kind": "meeting_summary",
                "chunk_index": None,
            },
            "score": 0.5,
        }
        all_docs = [*canonical, file_summary, meeting_summary]
        formatted = _format_docs(all_docs)
        sources = _extract_sources(all_docs)

        assert len(sources) == 4  # 2 chunks + 1 file summary + 1 meeting summary
        for n in range(1, 5):
            assert f"[{n}]" in formatted

        # Sources match formatted order
        assert sources[0]["source_kind"] == "text"
        assert sources[1]["source_kind"] == "text"
        assert sources[2]["source_kind"] == "file_summary"
        assert sources[3]["source_kind"] == "meeting_summary"

        # Summary refs appear in formatted output
        assert "File Summary" in formatted
        assert "Summary:" in formatted
