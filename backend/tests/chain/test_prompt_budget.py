"""Tests for global prompt budget (P1-4)."""

import sqlite3

from langchain_core.messages import HumanMessage

from src.services.chain._context import PipelineContext
from src.services.chain._steps_generate import _load_meeting_summaries_for_context, build_context


def _make_doc(text: str, meeting_id: int = 1, chunk: int = 0) -> dict:
    return {
        "content": text,
        "metadata": {"meeting_id": meeting_id, "chunk_index": chunk},
        "score": 0.5,
    }


def test_build_context_respects_total_budget_trims_docs(monkeypatch):
    """When PROMPT_TOTAL_BUDGET_TOKENS is small, docs should be trimmed."""
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.PROMPT_TOTAL_BUDGET_TOKENS", 400
    )
    monkeypatch.setattr("src.services.chain._steps_generate.settings.LLM_CONTEXT_WINDOW", 128_000)
    monkeypatch.setattr("src.services.chain._steps_generate.settings.LLM_MAX_TOKENS", 100)
    monkeypatch.setattr("src.services.chain._steps_generate.settings.LLM_PROMPT_RESERVE_TOKENS", 50)
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.MEMORY_CONTEXT_MAX_TOKENS", 200
    )
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.ENTITY_CONTEXT_MAX_TOKENS", 200
    )
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.SESSION_CONTEXT_MAX_TOKENS", 200
    )

    long_text = "lorem ipsum " * 200  # ~400 tokens each
    docs = [_make_doc(long_text, chunk=i) for i in range(10)]

    ctx = PipelineContext(question="q")
    ctx.docs = list(docs)
    ctx.memory_context = ""
    ctx.session_context = ""
    ctx.entity_context = ""
    ctx.web_context = ""
    ctx.history_messages = []

    build_context(ctx)

    assert len(ctx.docs) < len(docs), "Budget cap should have trimmed docs"


def test_build_context_no_trim_when_under_budget(monkeypatch):
    """Short context with generous budget should not trim."""
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.PROMPT_TOTAL_BUDGET_TOKENS", 10_000
    )
    monkeypatch.setattr("src.services.chain._steps_generate.settings.LLM_CONTEXT_WINDOW", 128_000)
    monkeypatch.setattr("src.services.chain._steps_generate.settings.LLM_MAX_TOKENS", 500)
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.LLM_PROMPT_RESERVE_TOKENS", 100
    )
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.MEMORY_CONTEXT_MAX_TOKENS", 800
    )
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.ENTITY_CONTEXT_MAX_TOKENS", 600
    )
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.SESSION_CONTEXT_MAX_TOKENS", 800
    )

    docs = [_make_doc("short doc content", chunk=i) for i in range(3)]

    ctx = PipelineContext(question="q")
    ctx.docs = list(docs)
    ctx.history_messages = [HumanMessage(content="short history")]

    build_context(ctx)

    assert len(ctx.docs) == 3


def test_build_context_budget_zero_disables_global_cap(monkeypatch):
    """Setting PROMPT_TOTAL_BUDGET_TOKENS=0 should fall back to context_window-only budget."""
    monkeypatch.setattr("src.services.chain._steps_generate.settings.PROMPT_TOTAL_BUDGET_TOKENS", 0)
    monkeypatch.setattr("src.services.chain._steps_generate.settings.LLM_CONTEXT_WINDOW", 128_000)
    monkeypatch.setattr("src.services.chain._steps_generate.settings.LLM_MAX_TOKENS", 500)
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.LLM_PROMPT_RESERVE_TOKENS", 100
    )
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.MEMORY_CONTEXT_MAX_TOKENS", 800
    )
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.ENTITY_CONTEXT_MAX_TOKENS", 600
    )
    monkeypatch.setattr(
        "src.services.chain._steps_generate.settings.SESSION_CONTEXT_MAX_TOKENS", 800
    )

    # Enough docs to exceed a 400-tok budget if total budget were active, but fit 128K
    long_text = "lorem ipsum " * 200
    docs = [_make_doc(long_text, chunk=i) for i in range(10)]
    ctx = PipelineContext(question="q")
    ctx.docs = list(docs)

    build_context(ctx)

    assert len(ctx.docs) == 10, "No trimming when total budget disabled and window is huge"


def test_load_meeting_summaries_handles_sqlite_row(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE meeting_summaries (meeting_id INTEGER PRIMARY KEY, summary TEXT)")
    conn.execute(
        "INSERT INTO meetings (id, title, created_at) VALUES (1, 'Demo Meeting', '2026-04-28 10:00:00')"
    )
    conn.execute(
        "INSERT INTO meeting_summaries (meeting_id, summary) VALUES (1, 'Summary text here.')"
    )
    conn.commit()

    monkeypatch.setattr("src.core.database.get_connection", lambda: conn)

    ctx = PipelineContext(question="q")
    ctx.docs = [{"content": "chunk", "metadata": {"meeting_id": 1}, "score": 0.1}]

    try:
        text, docs = _load_meeting_summaries_for_context(ctx)
        assert "## Meeting Summaries" in text
        assert "Demo Meeting" in text
        assert "2026-04-28" in text
        assert "Summary text here." in text
        assert any((d.get("metadata") or {}).get("source_kind") == "meeting_summary" for d in docs)
    finally:
        conn.close()
