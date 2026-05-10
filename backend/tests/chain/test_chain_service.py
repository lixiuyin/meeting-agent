"""Tests for chain service - RAG pipeline orchestration.

Tests cover:
- Intent classification (greeting, smalltalk detection)
- PipelineContext and PipelineResult
- Helper functions
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Set up test environment
os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from src.core import constants as constants_module

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"

from src.services import chain  # noqa: E402
from src.services.chain import (  # noqa: E402
    PipelineContext,
    PipelineResult,
    _casual_response,
    _classify_intent,
    _extract_sources,
    _format_docs,
    _format_memory_context,
    _is_trivially_short,
    cancel_background_tasks,
)


class TestIntentClassification:
    """Test query intent classification."""

    def test_classify_greeting_english(self):
        """Should classify English greetings."""
        assert _classify_intent("hello") == "casual"
        assert _classify_intent("hi") == "casual"
        assert _classify_intent("hey") == "casual"
        assert _classify_intent("good morning") == "casual"
        assert _classify_intent("good afternoon") == "casual"

    def test_classify_greeting_variants(self):
        """Should classify greeting variants."""
        assert _classify_intent("hello!!") == "casual"
        assert _classify_intent("HEY") == "casual"
        assert _classify_intent("good evening") == "casual"

    def test_classify_smalltalk(self):
        """Should classify smalltalk."""
        assert _classify_intent("thanks") == "casual"
        assert _classify_intent("thank you") == "casual"
        assert _classify_intent("ok") == "casual"
        assert _classify_intent("okay") == "casual"

    def test_classify_smalltalk_variants(self):
        """Should classify smalltalk variants."""
        assert _classify_intent("thanks!") == "casual"
        assert _classify_intent("got it") == "casual"
        assert _classify_intent("okay") == "casual"

    def test_classify_rag(self):
        """Should classify non-casual as rag."""
        queries = [
            "what was discussed in the meeting",
            "summarize the meeting",
            "what is the weather",
            "how does this work",
        ]
        for q in queries:
            assert _classify_intent(q) == "rag"


class TestCasualResponse:
    """Test casual response generation."""

    def test_casual_response_not_empty(self):
        """Should return non-empty response."""
        response = _casual_response("hello")
        assert len(response) > 0

    def test_casual_response_contains_agent(self):
        """Should identify as Meeting Agent."""
        response = _casual_response("hello")
        assert "agent" in response.lower() or "help" in response.lower()


class TestTriviallyShort:
    """Guards the streaming pipeline's small-talk short-circuit."""

    def test_short_english_smalltalk(self):
        """Only pure small-talk 2-word inputs (all words trivial) short-circuit.
        Inputs with any non-trivial word go to RAG. Single-word inputs are
        treated as search queries and routed to RAG."""
        assert _is_trivially_short("ok thanks")
        assert _is_trivially_short("yes please")
        assert _is_trivially_short("hey yo")
        assert not _is_trivially_short("yo bro")
        assert not _is_trivially_short("thanks dude")
        assert not _is_trivially_short("summarize meeting")
        assert not _is_trivially_short("yo")
        assert not _is_trivially_short("cool")
        assert not _is_trivially_short("Rosie")

    def test_chinese_question_not_short_circuited(self):
        """Regression: CJK questions used to be misclassified because
        whitespace-tokenization counts them as <=2 'words'. They must reach RAG.
        """
        assert not _is_trivially_short("Dario和Alex分别讲了什么?")
        assert not _is_trivially_short("讲了什么")
        assert not _is_trivially_short("讲了什么？")  # noqa: RUF001
        assert not _is_trivially_short("会议总结")

    def test_japanese_korean_not_short_circuited(self):
        """Hiragana/Katakana/Hangul should also bypass the short-circuit."""
        assert not _is_trivially_short("会議の要約")
        assert not _is_trivially_short("회의 요약")
        assert not _is_trivially_short("ミーティング")

    def test_question_marker_blocks_short_circuit(self):
        """English questions with '?' should always go to RAG even if short."""
        assert not _is_trivially_short("what?")
        assert not _is_trivially_short("why?")
        assert not _is_trivially_short("ok?")

    def test_slash_command_not_short_circuited(self):
        """Slash-prefixed commands should never be treated as small talk."""
        assert not _is_trivially_short("/help")
        assert not _is_trivially_short("/reset now")

    def test_empty_input(self):
        """Empty input should not trigger the canned response path."""
        assert not _is_trivially_short("")
        assert not _is_trivially_short("   ")

    def test_long_english_query(self):
        """Multi-word English queries must reach the RAG pipeline."""
        assert not _is_trivially_short("summarize the meeting please")


class TestPipelineContext:
    """Test PipelineContext dataclass."""

    def test_context_creation(self):
        """Should create context with required fields."""
        ctx = PipelineContext(
            question="What was discussed?",
            session_id="test-session",
            user_id="test-user",
        )
        assert ctx.question == "What was discussed?"
        assert ctx.session_id == "test-session"
        assert ctx.user_id == "test-user"
        assert ctx.docs == []
        assert ctx.memory_context == ""

    def test_context_with_optional_fields(self):
        """Should create context with optional fields."""
        ctx = PipelineContext(
            question="What was discussed?",
            session_id="test-session",
            user_id="test-user",
            meeting_ids=[1, 2, 3],
            use_web_search=True,
        )
        assert ctx.meeting_ids == [1, 2, 3]
        assert ctx.use_web_search is True


class TestPipelineResult:
    """Test PipelineResult dataclass."""

    def test_result_creation(self):
        """Should create result with required fields."""
        result = PipelineResult(
            answer="The meeting discussed project plans.",
            session_id="test-session",
            sources=[{"meeting_id": 1, "meeting_title": "Test Meeting"}],
        )
        assert result.answer == "The meeting discussed project plans."
        assert result.session_id == "test-session"
        assert len(result.sources) == 1


class TestBackgroundTasks:
    """Test background task management."""

    def test_cancel_empty_tasks(self):
        """Should handle empty task set."""
        chain._background_tasks.clear()
        # Should not raise exception
        cancel_background_tasks()
        assert len(chain._background_tasks) == 0


class TestFormatDocs:
    """Test document formatting."""

    def test_format_empty_docs(self):
        """Should handle empty document list."""
        result = _format_docs([])
        assert result == "No relevant meeting content found."

    def test_format_single_doc(self):
        """Should format single document."""
        docs = [{"content": "Meeting content here", "metadata": {"meeting_id": 1}}]
        result = _format_docs(docs)
        assert "Meeting content here" in result

    def test_format_multiple_docs(self):
        """Should format multiple documents."""
        docs = [
            {"content": "Doc 1", "metadata": {"meeting_id": 1}},
            {"content": "Doc 2", "metadata": {"meeting_id": 2}},
        ]
        result = _format_docs(docs)
        assert "Doc 1" in result
        assert "Doc 2" in result

    def test_format_strips_speaker_artifact(self):
        """Should strip [Speaker] tokens from chunk content for the LLM context."""
        docs = [
            {
                "content": "Alex: [Speaker] We found that...",
                "metadata": {"meeting_id": 1},
            }
        ]
        result = _format_docs(docs)
        assert "[Speaker]" not in result
        assert "Alex: We found that..." in result


class TestFormatMemoryContext:
    """Test memory context formatting."""

    def test_format_empty_memories(self):
        """Should handle empty memories."""
        result = _format_memory_context([])
        assert result == ""

    def test_format_none_memories(self):
        """Should handle None memories."""
        result = _format_memory_context(None)
        assert result == ""

    def test_format_memories(self):
        """Should format memories."""
        memories = [
            {"key": "preference", "value": "likes detailed summaries"},
            {"key": "role", "value": "project manager"},
        ]
        result = _format_memory_context(memories)
        assert "preference" in result or "likes detailed summaries" in result


class TestExtractSources:
    """Test source extraction."""

    def test_extract_from_docs(self):
        """Should extract sources from documents."""
        docs = [
            {
                "content": "Content",
                "metadata": {"meeting_id": 1, "title": "Meeting 1"},
            },
            {
                "content": "More content",
                "metadata": {"meeting_id": 2, "title": "Meeting 2"},
            },
        ]
        sources = _extract_sources(docs)
        assert len(sources) == 2
        assert sources[0]["meeting_id"] == 1

    def test_extract_handles_missing_metadata(self):
        """Should handle documents with missing metadata."""
        docs = [{"content": "Content", "metadata": {}}]
        sources = _extract_sources(docs)
        assert len(sources) == 0

    def test_extract_handles_missing_title(self):
        """Should handle documents without title."""
        docs = [{"content": "Content", "metadata": {"meeting_id": 1}}]
        sources = _extract_sources(docs)
        assert sources[0]["meeting_title"] == "Meeting#1"

    def test_extract_strips_speaker_artifact(self):
        """Source content should not contain [Speaker] tokens."""
        docs = [
            {
                "content": "Alex: [Speaker] We found that...",
                "metadata": {"meeting_id": 1, "title": "Meeting 1"},
            }
        ]
        sources = _extract_sources(docs)
        assert "[Speaker]" not in sources[0]["content"]
        assert sources[0]["content"] == "Alex: We found that..."


class TestScopedContextLoading:
    @pytest.mark.asyncio
    async def test_load_memories_respects_meeting_filter(self):
        from src.core.config import settings
        from src.services.chain._steps_context import load_memories

        ctx = PipelineContext(
            question="q",
            user_id="scope_ctx_user",
            meeting_ids=[101],
            file_ids=[501],
        )
        with (
            patch("src.services.memory.memory_service.get", return_value=None),
            patch(
                "src.services.memory.memory_service.search_semantic",
                new=AsyncMock(return_value=[]),
            ) as search_mock,
        ):
            await load_memories(ctx)

        search_mock.assert_awaited_once_with(
            "scope_ctx_user",
            query="q",
            limit=min(settings.MEMORY_MAX_CONTEXT_ITEMS, 8),
            min_importance=3,
            meeting_ids=[101],
            file_ids=[501],
        )

    @pytest.mark.asyncio
    async def test_load_entity_context_filtered(self):
        from src.services.chain._steps_context import load_entity_context

        ctx = PipelineContext(question="q", user_id="scope_ctx_user", meeting_ids=[101])
        with patch(
            "src.services.knowledge_graph.kg_service.get_entity_context",
            new=AsyncMock(return_value=""),
        ) as entity_mock:
            await load_entity_context(ctx)

        entity_mock.assert_awaited_once_with(
            "scope_ctx_user", "q", meeting_ids=[101], file_ids=None
        )

    @pytest.mark.asyncio
    async def test_load_session_context_filtered_by_meeting(self, monkeypatch: pytest.MonkeyPatch):
        from src.core.config import settings
        from src.services.chain._steps_context import load_session_context

        monkeypatch.setattr(settings, "SESSION_CONTEXT_SKIP_THRESHOLD", 0)
        ctx = PipelineContext(
            question="q", user_id="scope_ctx_user", session_id="current", meeting_ids=[101]
        )
        with patch(
            "src.services.memory.session_summary_service.search_sessions",
            new=AsyncMock(return_value=[]),
        ) as session_mock:
            await load_session_context(ctx)

        session_mock.assert_awaited_once_with(
            "scope_ctx_user",
            query="q",
            limit=settings.SESSION_SUMMARY_MAX_ITEMS,
            meeting_ids=[101],
        )

    @pytest.mark.asyncio
    async def test_load_session_context_skips_for_tight_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from src.core.config import settings
        from src.services.chain._steps_context import load_session_context

        monkeypatch.setattr(settings, "SESSION_CONTEXT_SKIP_THRESHOLD", 3)
        ctx = PipelineContext(
            question="q", user_id="scope_ctx_user", session_id="current", meeting_ids=[101]
        )
        with patch(
            "src.services.memory.session_summary_service.search_sessions",
            new=AsyncMock(return_value=[]),
        ) as session_mock:
            await load_session_context(ctx)

        session_mock.assert_not_awaited()

    def test_build_context_caps_non_meeting_sections(self, monkeypatch: pytest.MonkeyPatch):
        from src.core.config import settings
        from src.services.chain._steps_generate import build_context
        from src.services.tokenizer import count_tokens

        monkeypatch.setattr(settings, "MEMORY_CONTEXT_MAX_TOKENS", 40)
        monkeypatch.setattr(settings, "ENTITY_CONTEXT_MAX_TOKENS", 30)
        monkeypatch.setattr(settings, "SESSION_CONTEXT_MAX_TOKENS", 40)
        monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 16000)
        monkeypatch.setattr(settings, "LLM_PROMPT_RESERVE_TOKENS", 0)
        monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 64)

        ctx = PipelineContext(question="What happened?")
        ctx.memory_context = "memory " * 300
        ctx.entity_context = "entity " * 240
        ctx.session_context = "session " * 300
        ctx.docs = [
            {
                "content": "meeting " * 300,
                "metadata": {"meeting_id": 1, "title": "Scoped Meeting"},
                "score": 0.1,
            }
        ]

        build_context(ctx)

        model = settings.LLM_MODEL
        assert count_tokens(ctx.memory_context, model) <= settings.MEMORY_CONTEXT_MAX_TOKENS
        assert count_tokens(ctx.entity_context, model) <= settings.ENTITY_CONTEXT_MAX_TOKENS
        assert count_tokens(ctx.session_context, model) <= settings.SESSION_CONTEXT_MAX_TOKENS

    @pytest.mark.asyncio
    async def test_context_loader_timeout(self, monkeypatch: pytest.MonkeyPatch, caplog):
        from src.core.config import settings
        from src.services.chain._api import _run_pipeline

        async def _slow_session_context(_ctx):
            await asyncio.sleep(1.0)

        monkeypatch.setattr(settings, "CONTEXT_LOAD_TIMEOUT_S", 0.05)
        monkeypatch.setattr(settings, "WEB_SEARCH_TIMEOUT_S", 0.05)

        monkeypatch.setattr(
            "src.services.chain._api.ensure_session",
            lambda c: setattr(c, "session_id", "timeout-session"),
        )
        monkeypatch.setattr(
            "src.services.chain._api.rewrite_query_step", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            "src.services.chain._api._prewarm_query_embedding",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "src.services.chain._api.retrieve_documents",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr("src.services.chain._api.rerank_documents", lambda _ctx: None)
        monkeypatch.setattr(
            "src.services.chain._api.suppress_near_duplicates",
            lambda _ctx: None,
        )
        monkeypatch.setattr("src.services.chain._api.load_memories", AsyncMock(return_value=None))
        monkeypatch.setattr("src.services.chain._api.load_session_context", _slow_session_context)
        monkeypatch.setattr(
            "src.services.chain._api.load_entity_context", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            "src.services.chain._api.perform_web_search", AsyncMock(return_value=None)
        )
        monkeypatch.setattr("src.services.chain._api.load_history", AsyncMock(return_value=None))
        monkeypatch.setattr("src.services.chain._api.build_context", lambda _ctx: None)
        monkeypatch.setattr(
            "src.services.chain._api.generate_answer",
            AsyncMock(side_effect=lambda c, _skill=None: setattr(c, "answer", "ok")),
        )
        monkeypatch.setattr("src.services.chain._api.save_messages", lambda _ctx: None)
        monkeypatch.setattr("src.services.chain._api.schedule_fact_extraction", lambda _ctx: None)

        caplog.set_level("WARNING")
        ctx = PipelineContext(question="timeout test")
        started = time.monotonic()
        await _run_pipeline(ctx)
        elapsed = time.monotonic() - started

        assert elapsed < 0.5
        assert "timed out" in caplog.text


class TestContextBranchTimeout:
    """Test _context_branch_timeout returns the configured value."""

    def test_returns_configured_timeout(self, monkeypatch):
        from src.core.config import settings
        from src.services.chain._api import _context_branch_timeout

        monkeypatch.setattr(settings, "CONTEXT_LOAD_TIMEOUT_S", 10.0)
        ctx = PipelineContext(question="q")
        assert _context_branch_timeout(ctx) == 10.0

    def test_returns_configured_timeout_with_file_ids(self, monkeypatch):
        from src.core.config import settings
        from src.services.chain._api import _context_branch_timeout

        monkeypatch.setattr(settings, "CONTEXT_LOAD_TIMEOUT_S", 6.0)
        ctx = PipelineContext(question="q", file_ids=["f1"])
        assert _context_branch_timeout(ctx) == 6.0

    def test_full_timeout_when_web_search_enabled(self, monkeypatch):
        from src.core.config import settings
        from src.services.chain._api import _context_branch_timeout

        monkeypatch.setattr(settings, "CONTEXT_LOAD_TIMEOUT_S", 10.0)
        ctx = PipelineContext(question="q", file_ids=["f1"], use_web_search=True)
        assert _context_branch_timeout(ctx) == 10.0

    @pytest.mark.asyncio
    async def test_context_step_timeout_logs_warning_with_file_ids(self, monkeypatch, caplog):
        """Context step timeout fires the WARNING when file_ids forces the tight floor."""
        from src.core.config import settings
        from src.services.chain._api import _run_pipeline

        async def _slow_memories(_ctx):
            await asyncio.sleep(1.0)

        async def _slow_entity(_ctx):
            await asyncio.sleep(1.0)

        monkeypatch.setattr(settings, "CONTEXT_LOAD_TIMEOUT_S", 0.05)
        monkeypatch.setattr(settings, "WEB_SEARCH_TIMEOUT_S", 0.05)

        monkeypatch.setattr(
            "src.services.chain._api.ensure_session",
            lambda c: setattr(c, "session_id", "timeout-session"),
        )
        monkeypatch.setattr(
            "src.services.chain._api.rewrite_query_step", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            "src.services.chain._api._prewarm_query_embedding",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "src.services.chain._api.retrieve_documents",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr("src.services.chain._api.rerank_documents", lambda _ctx: None)
        monkeypatch.setattr(
            "src.services.chain._api.suppress_near_duplicates",
            lambda _ctx: None,
        )
        monkeypatch.setattr("src.services.chain._api.load_memories", _slow_memories)
        monkeypatch.setattr(
            "src.services.chain._api.load_session_context", AsyncMock(return_value=None)
        )
        monkeypatch.setattr("src.services.chain._api.load_entity_context", _slow_entity)
        monkeypatch.setattr(
            "src.services.chain._api.perform_web_search", AsyncMock(return_value=None)
        )
        monkeypatch.setattr("src.services.chain._api.load_history", AsyncMock(return_value=None))
        monkeypatch.setattr("src.services.chain._api.build_context", lambda _ctx: None)
        monkeypatch.setattr(
            "src.services.chain._api.generate_answer",
            AsyncMock(side_effect=lambda c, _skill=None: setattr(c, "answer", "ok")),
        )
        monkeypatch.setattr("src.services.chain._api.save_messages", lambda _ctx: None)
        monkeypatch.setattr("src.services.chain._api.schedule_fact_extraction", lambda _ctx: None)

        caplog.set_level(logging.WARNING)
        ctx = PipelineContext(question="timeout test", file_ids=["f1"])
        await _run_pipeline(ctx)

        assert any("context step 'memories' timed out" in r.message for r in caplog.records), (
            f"Expected memories timeout warning, got: {[r.message for r in caplog.records]}"
        )
        assert any("context step 'entity' timed out" in r.message for r in caplog.records), (
            f"Expected entity timeout warning, got: {[r.message for r in caplog.records]}"
        )


def test_rerank_documents_skips_for_single_file_scope(monkeypatch: pytest.MonkeyPatch):
    from src.services.chain._steps_retrieve import rerank_documents

    monkeypatch.setattr("src.services.chain._steps_retrieve.settings.RERANKER_BINDING", "cohere")
    monkeypatch.setattr("src.services.chain._steps_retrieve.settings.RERANKER_TOP_N", 1)

    ctx = PipelineContext(question="q", file_ids=[99])
    ctx.docs = [
        {"content": "a", "metadata": {"meeting_id": 1}, "score": 0.9},
        {"content": "b", "metadata": {"meeting_id": 1}, "score": 0.8},
        {"content": "c", "metadata": {"meeting_id": 1}, "score": 0.7},
    ]

    with patch("src.services.rag.rerank") as rerank_mock:
        rerank_documents(ctx)
        rerank_mock.assert_not_called()

    rerank_spans = [span for span in ctx.trace.spans if span.label == "rerank"]
    assert rerank_spans
    assert rerank_spans[-1].skipped is True
