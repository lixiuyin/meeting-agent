"""Tests for streaming pipeline internals (StreamBus error handling, disconnect, terminal events)."""

import asyncio

from src.core.exceptions import LLMEmptyResponseError
from src.services.chain._api_stream import (
    _normalise_stream_content,
    _should_skip_stream_rerank,
    _stream_user_error_message,
)
from src.services.chain._context import PipelineContext
from src.services.stream_bus import StreamBus


def test_stream_content_normalization_omits_reasoning_blocks():
    event = type(
        "Chunk",
        (),
        {
            "content": [
                {"type": "reasoning", "text": "private reasoning"},
                {"type": "text", "text": "Visible answer"},
            ]
        },
    )()

    assert _normalise_stream_content(event) == "Visible answer"


def test_empty_provider_response_has_explicit_terminal_error():
    error = _stream_user_error_message(LLMEmptyResponseError("empty"))

    assert error["code"] == "EMPTY_LLM_RESPONSE"
    assert "no usable answer" in error["message"]


def test_latency_guard_applies_to_simple_facts_across_profiles(monkeypatch):
    monkeypatch.setattr(
        "src.services.chain._api_stream.settings.CHAT_STREAM_LATENCY_GUARD_ENABLED", True
    )
    assert _should_skip_stream_rerank(PipelineContext(question="Who owns Atlas?"))
    assert _should_skip_stream_rerank(
        PipelineContext(question="Who owns Atlas?", retrieval_profile="fast")
    )
    for question in (
        "AI会取代全部工作吗?",
        "比较所有会议并解释它们的因果关系",
        "这些会议主要讲了什么内容?",
    ):
        assert not _should_skip_stream_rerank(
            PipelineContext(question=question, retrieval_profile="fast")
        )


def test_latency_guard_respects_operator_and_query_boundaries(monkeypatch):
    ctx = PipelineContext(question="Who owns Atlas?")
    monkeypatch.setattr(
        "src.services.chain._api_stream.settings.CHAT_STREAM_LATENCY_GUARD_ENABLED", False
    )
    assert not _should_skip_stream_rerank(ctx)

    monkeypatch.setattr(
        "src.services.chain._api_stream.settings.CHAT_STREAM_LATENCY_GUARD_ENABLED", True
    )
    assert not _should_skip_stream_rerank(
        PipelineContext(question="Who owns Atlas?", use_web_search=True, web_search_mode="always")
    )


class TestStreamBus:
    def test_terminal_done_event(self):
        """StreamBus yields all queued events including terminal 'done'."""
        bus = StreamBus()

        async def _run():
            bus.emit_step("retrieve", "start")
            bus.emit_step("retrieve", "done", duration_ms=123.4)
            bus.emit_token("hello")
            bus.emit_done(session_id="test-session")

            events = [e async for e in bus]
            return events

        events = asyncio.run(_run())
        assert len(events) == 4
        assert events[0]["type"] == "step"
        assert events[1]["type"] == "step"
        assert events[2] == {"type": "token", "content": "hello", "seq": events[2]["seq"]}
        assert events[3]["type"] == "done"
        assert events[3]["session_id"] == "test-session"

    def test_error_event_does_not_block_stream(self):
        """An error event is emitted and reaches the consumer."""
        bus = StreamBus()

        async def _run():
            bus.emit_token("a")
            bus.emit_error("test error", code="INTERNAL", detail="boom")
            # Don't emit done — error IS a terminal event
            bus.close()

            events = [e async for e in bus]
            return events

        events = asyncio.run(_run())
        types = [e["type"] for e in events]
        assert types == ["token", "error"]

    def test_done_with_error_flag(self):
        """emit_done with error=True signals the frontend to terminate."""
        bus = StreamBus()

        async def _run():
            bus.emit_token("a")
            bus.emit_done(session_id="err-session", error=True)

            events = [e async for e in bus]
            return events

        events = asyncio.run(_run())
        assert len(events) == 2
        assert events[1]["type"] == "done"
        assert events[1].get("error") is True

    def test_client_disconnect_does_not_lose_close(self):
        """When the consumer breaks early, the bus still closes cleanly."""
        bus = StreamBus()

        async def _run():
            bus.emit_token("a")
            bus.emit_token("b")

            collected = []
            async for event in bus:
                collected.append(event)
                break  # client disconnects after first event

            bus.close()
            return collected

        collected = asyncio.run(_run())
        assert len(collected) == 1
        assert collected[0]["type"] == "token"

    def test_emit_after_close_is_noop(self):
        """Emitting after bus.close() does not raise or queue events."""
        bus = StreamBus()
        bus.close()
        bus.emit_token("x")  # should not raise

        async def _drain():
            return [e async for e in bus]

        events = asyncio.run(_drain())
        assert events == []

    def test_double_close_is_safe(self):
        """Calling close() twice is idempotent."""
        bus = StreamBus()
        bus.close()
        bus.close()  # should not raise

    def test_aclose_timeout_handling(self):
        """Bus handles async generator close without hanging."""
        bus = StreamBus()
        bus.emit_token("test")
        bus.emit_done(session_id="s1")

        async def _consume_and_close():
            events = []
            async for event in bus:
                events.append(event)
            return events

        events = asyncio.run(asyncio.wait_for(_consume_and_close(), timeout=5.0))
        assert len(events) == 2
        assert events[-1]["type"] == "done"

    def test_context_manager_closes_bus(self):
        """Using StreamBus as async context manager ensures close on exit."""

        async def _run():
            async with StreamBus() as bus:
                bus.emit_token("x")
                bus.emit_done(session_id="ctx-test")
                events = [e async for e in bus]
            return events, bus._closed

        events, closed = asyncio.run(_run())
        assert len(events) == 2
        assert closed is True
        assert events[-1]["type"] == "done"

    def test_structural_events_preserve_emission_order(self):
        """Tokens and structural events keep their original emission order."""
        bus = StreamBus()

        async def _run():
            bus.emit_token("a")
            bus.emit_sources([{"id": 1}])
            bus.emit_trace({"span": "test"})
            bus.emit_token("b")
            bus.emit_done(session_id="s")

            events = [e async for e in bus]
            return events

        events = asyncio.run(_run())
        assert [(event["type"], event.get("content")) for event in events] == [
            ("token", "a"),
            ("sources", None),
            ("trace", None),
            ("token", "b"),
            ("done", None),
        ]

    def test_degraded_status_is_a_separate_structural_event(self):
        """Degradation metadata must not be mixed into the answer token text."""
        bus = StreamBus()

        async def _run():
            bus.emit_status("degraded", reason="fast_path_timeout")
            bus.emit_token("Relevant source excerpts (partial result):")
            bus.emit_done(session_id="s")
            return [event async for event in bus]

        events = asyncio.run(_run())
        assert events[0]["type"] == "status"
        assert events[0]["status"] == "degraded"
        assert events[0]["reason"] == "fast_path_timeout"
        assert events[1]["type"] == "token"

    def test_error_terminal_event_yields(self):
        """When only emit_error is called (no done), the error is yielded."""
        bus = StreamBus()

        async def _run():
            bus.emit_sources([{"id": 1}])
            bus.emit_error("something broke", code="INTERNAL")
            bus.close()  # close without done — error IS the terminal event

            events = [e async for e in bus]
            return events

        events = asyncio.run(_run())
        types = [e["type"] for e in events]
        assert types[0] == "sources"
        assert types[1] == "error"

    def test_first_terminal_error_cannot_be_overridden_by_done(self):
        """A later done must never disguise an already-reported failure."""
        bus = StreamBus()

        async def _run():
            bus.emit_error("temporary error", code="TRANSIENT")
            bus.emit_done(session_id="final", error=True)

            events = [e async for e in bus]
            return events

        events = asyncio.run(_run())
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["code"] == "TRANSIENT"
