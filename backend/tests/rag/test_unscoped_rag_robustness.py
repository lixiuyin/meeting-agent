"""Tests for unscoped global RAG robustness.

Covers fixes that eliminate the "no memories + no scope -> empty RAG" failure:
- D1: Skip diversity when only one meeting is present in results.
- D2: Retry with raw question when rewritten query returns zero docs.
- D3: Unscoped zero-result warning is emitted.
- Diversity still applies when multiple meetings are present.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.rag._retriever import _diversify_by_meeting


def _make_doc(meeting_id: int, content: str, score: float = 0.5) -> dict:
    return {
        "content": content,
        "metadata": {"meeting_id": meeting_id},
        "score": score,
    }


class TestSingleMeetingDiversitySkip:
    """D1: _diversify_by_meeting should not demote chunks when only one meeting is present."""

    def test_single_meeting_all_chunks_preserved(self) -> None:
        docs = [_make_doc(1, f"chunk {i}", score=i * 0.1) for i in range(10)]
        result = _diversify_by_meeting(docs, max_per_meeting=3)
        # All 10 chunks should be in the result (no demotion to tail)
        assert len(result) == 10
        # First 3 in head, rest in tail but still present
        head_ids = [d["metadata"]["meeting_id"] for d in result[:3]]
        assert all(mid == 1 for mid in head_ids)

    def test_single_meeting_preserves_order(self) -> None:
        docs = [_make_doc(1, f"chunk {i}", score=float(10 - i)) for i in range(5)]
        result = _diversify_by_meeting(docs, max_per_meeting=3)
        # Head: first 3 (by original order), tail: last 2
        assert result[0]["content"] == "chunk 0"
        assert result[1]["content"] == "chunk 1"
        assert result[2]["content"] == "chunk 2"


class TestMultiMeetingDiversity:
    """Diversity should cap per-meeting contribution when multiple meetings present."""

    def test_diversity_caps_per_meeting(self) -> None:
        # 6 chunks from meeting 1, 6 from meeting 2
        docs = [_make_doc(1, f"m1-chunk {i}") for i in range(6)]
        docs += [_make_doc(2, f"m2-chunk {i}") for i in range(6)]
        result = _diversify_by_meeting(docs, max_per_meeting=3)

        # Head should have exactly 3 from each meeting (6 total)
        head = result[:6]
        m1_count = sum(1 for d in head if d["metadata"]["meeting_id"] == 1)
        m2_count = sum(1 for d in head if d["metadata"]["meeting_id"] == 2)
        assert m1_count == 3
        assert m2_count == 3

        # Tail should have the remaining 6
        assert len(result) == 12

    def test_diversity_three_meetings(self) -> None:
        docs = [_make_doc(i, f"m{i}-chunk") for i in [1, 1, 1, 2, 2, 2, 3, 3, 3]]
        result = _diversify_by_meeting(docs, max_per_meeting=2)

        head = result[:6]  # 2 per meeting * 3 meetings
        m1 = sum(1 for d in head if d["metadata"]["meeting_id"] == 1)
        m2 = sum(1 for d in head if d["metadata"]["meeting_id"] == 2)
        m3 = sum(1 for d in head if d["metadata"]["meeting_id"] == 3)
        assert m1 == 2
        assert m2 == 2
        assert m3 == 2


class TestRawQuestionRetry:
    """D2: When rewritten query returns zero docs (unscoped), retry with raw question."""

    @pytest.mark.asyncio
    async def test_retry_on_zero_rewritten_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.core.config import settings
        from src.core.trace import TraceContext
        from src.services.chain._context import PipelineContext
        from src.services.chain._steps_retrieve import retrieve_documents

        ctx = PipelineContext(
            question="summarize the meetings",
            user_id="test_user",
        )
        ctx.rewritten_query = "comprehensive summary of all meeting discussions and action items"
        ctx.trace = TraceContext()

        # Track which queries triggered retrieve.  Under the v4 architecture the
        # broad recall path can call retrieve from multiple sites (funnel narrow
        # wide fetch + fair retrieval per file); the retry only fires when the
        # rewritten query produces zero docs across the whole pipeline.
        queries_seen: list[str] = []

        def mock_retrieve(*args, **kwargs):
            query = args[0] if args else kwargs.get("query")
            queries_seen.append(query)
            if query == ctx.rewritten_query:
                return [], None  # Rewritten returns 0 across all retrieve sites
            return [_make_doc(1, "meeting content")], None

        async def _no_route(*_args, **_kwargs):
            return None

        async def _no_enum(_mids):
            return []

        monkeypatch.setattr(settings, "RAG_SIBLING_CORETRIEVE_ENABLED", False)
        monkeypatch.setattr(
            "src.services.rag._scoping_strategies._route_scope_files_with_scores", _no_route
        )
        monkeypatch.setattr(
            "src.services.rag._scoping_strategies._route_scope_files_via_summary", _no_route
        )
        monkeypatch.setattr("src.services.rag._scoping_strategies._enumerate_scope_files", _no_enum)
        with (
            patch("src.services.chain._steps_retrieve.retrieve", side_effect=mock_retrieve),
            patch("src.services.rag._funnel_narrow.retrieve", side_effect=mock_retrieve),
            patch("src.services.rag._fair_retriever.retrieve", side_effect=mock_retrieve),
        ):
            await retrieve_documents(ctx)

        assert len(ctx.docs) >= 1
        # Retry semantics: rewritten query must have been tried first, then
        # the raw question must follow when rewritten returned nothing.
        assert ctx.rewritten_query in queries_seen
        assert ctx.question in queries_seen

    @pytest.mark.asyncio
    async def test_no_retry_when_scoped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.core.config import settings
        from src.core.trace import TraceContext
        from src.services.chain._context import PipelineContext
        from src.services.chain._steps_retrieve import retrieve_documents

        ctx = PipelineContext(
            question="summarize",
            user_id="test_user",
            meeting_ids=[1],
            file_ids=[9],
        )
        ctx.rewritten_query = "comprehensive summary"
        ctx.trace = TraceContext()

        queries_seen: list[str] = []

        def mock_retrieve(*args, **kwargs):
            query = args[0] if args else kwargs.get("query")
            queries_seen.append(query)
            return [], None

        monkeypatch.setattr(settings, "RAG_SIBLING_CORETRIEVE_ENABLED", False)
        with patch("src.services.chain._steps_retrieve.retrieve", side_effect=mock_retrieve):
            await retrieve_documents(ctx)

        # Scoped path with file_ids set: retry guard requires no scope, so the
        # raw question must NOT be tried.  Only the rewritten query is seen.
        assert ctx.question not in queries_seen
        assert ctx.rewritten_query in queries_seen


class TestUnscopedZeroResultWarning:
    """D3: Unscoped zero-result warning is emitted."""

    def test_warn_unscoped_zero_results_called(self) -> None:
        from src.services.rag._filters import _warn_unscoped_zero_results

        # Just verify the function exists and can be called without error
        _warn_unscoped_zero_results(k=5, vectorstore_doc_count=100, embedding_ok=True, bm25_ok=True)

    def test_warn_with_none_doc_count(self) -> None:
        from src.services.rag._filters import _warn_unscoped_zero_results

        _warn_unscoped_zero_results(k=10, vectorstore_doc_count=None)
