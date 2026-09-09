"""Regressions for incomplete streams, output quality and source formatting."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.chain._api_stream import _emit_stream, _preserve_incomplete_stream
from src.services.chain._context import PipelineContext
from src.services.chain._formatting import _extract_sources, _format_docs, _scrub_display_content
from src.services.stream_bus import StreamBus


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [" ", "\n", "\n\n", ""])
async def test_timeout_after_partial_output_never_repeats_prefix_or_dumps_sources(suffix):
    ctx = PipelineContext(question="这些会议主要讲了什么内容?", docs=[{"content": "DBP | CHOL"}])
    bus = StreamBus()
    prefix = "这三个会议各有不同主题。\n\n## Meeting #1 Next\n\n围绕 Opus" + suffix

    async def stream():
        yield SimpleNamespace(content=prefix)
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(_emit_stream(bus, ctx, stream()), timeout=0.01)
    answer = _preserve_incomplete_stream(bus, ctx)
    bus.emit_done("test")
    events = [event async for event in bus]
    visible = "".join(e["content"] for e in events if e["type"] == "token")
    assert visible == prefix == answer == ctx.answer
    assert ctx.degraded and ctx.degradation_reason == "generation_timeout"
    assert any(e["type"] == "status" and e["status"] == "degraded" for e in events)


@pytest.mark.asyncio
async def test_timeout_before_visible_output_does_not_claim_irrelevant_excerpts_answer_question():
    ctx = PipelineContext(question="这些会议讲了什么?", docs=[{"content": "DBP | CHOL"}])
    bus = StreamBus()
    answer = _preserve_incomplete_stream(bus, ctx)
    bus.emit_done("test")
    events = [e async for e in bus]
    assert "请重试" in answer
    assert "DBP" not in answer
    assert len([e for e in events if e["type"] == "token"]) == 1


@pytest.mark.asyncio
async def test_guarded_fact_timeout_returns_labelled_source_excerpt_with_citation():
    ctx = PipelineContext(question="Who owns the release?")
    bus = StreamBus()
    answer = _preserve_incomplete_stream(
        bus,
        ctx,
        evidence_sources=[{"content": "Priya Nair owns ORBIT-742."}],
    )
    bus.emit_done("test")
    events = [event async for event in bus]

    assert "not synthesized" in answer
    assert "[1] Priya Nair owns ORBIT-742." in answer
    assert ctx.degraded and ctx.degradation_reason == "generation_timeout"
    assert any(event["type"] == "status" for event in events)


@pytest.mark.asyncio
async def test_provider_output_limit_marks_answer_incomplete_even_on_clean_stream_end():
    ctx = PipelineContext(question="Summarize the meeting")
    bus = StreamBus()

    async def stream():
        yield SimpleNamespace(content="Partial answer")
        yield SimpleNamespace(content="", response_metadata={"finish_reason": "length"})

    await _emit_stream(bus, ctx, stream())
    assert ctx.degraded and ctx.degradation_reason == "output_limit"


def test_context_and_public_source_preview_strip_index_only_prefix():
    doc = {
        "content": "[Retrieval context: meeting=Jobs]\nThe source text.",
        "metadata": {"meeting_id": 1, "file_id": 2, "chunk_index": 0},
    }
    assert "Retrieval context" not in _format_docs([doc])
    assert "Retrieval context" not in _extract_sources([doc])[0]["content"]


def test_display_cleanup_preserves_markdown_table_and_paragraph_boundaries():
    source = (
        "[Retrieval context: file=table.pdf]\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nParagraph."
    )
    assert _scrub_display_content(source) == source.split("\n", 1)[1]


@pytest.mark.asyncio
async def test_incomplete_answer_cannot_create_anchor_or_fact_job(monkeypatch):
    from src.services.chain._steps_generate import schedule_fact_extraction
    from src.services.chain._steps_retrieve import commit_anchor_for_success

    anchor, job = Mock(), Mock()
    monkeypatch.setattr("src.services.chain._steps_retrieve._write_anchor_from_docs", anchor)
    monkeypatch.setattr("src.services.jobs.enqueue_durable_job", job)
    ctx = PipelineContext(question="What happened?", answer="Partial", degraded=True)
    commit_anchor_for_success(ctx)
    await schedule_fact_extraction(ctx)
    anchor.assert_not_called()
    job.assert_not_called()


def test_chinese_overview_uses_summary_plan_and_broad_recall():
    from src.services.rag._query import determine_adaptive_top_k, is_fast_query
    from src.services.rag._query_plan import infer_query_intent

    question = "这些会议主要讲了什么内容?"
    assert infer_query_intent(question) == "summary"
    assert not is_fast_query(question, include_summary=True)
    assert determine_adaptive_top_k(question, None) >= 8
    assert determine_adaptive_top_k(question, 2) == 2


def test_truncated_reasoning_and_orphan_tags_do_not_leak():
    from src.services.llm._parsing import strip_thinking_blocks

    assert strip_thinking_blocks("<THINK>unfinished private reasoning") == ""
    assert strip_thinking_blocks("</think>Public summary") == "Public summary"
    assert strip_thinking_blocks("<thinking>private</thinking>Visible") == "Visible"
