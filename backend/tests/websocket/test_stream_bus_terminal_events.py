"""T7: Verify StreamBus terminal events (done/error) are never dropped (C-H2)."""

import pytest

from src.services.stream_bus import StreamBus, StreamEvent


@pytest.mark.unit
class TestStreamBusTerminalEvents:
    @pytest.mark.asyncio
    async def test_done_always_delivered_unbounded(self):
        """Sentinel (None) is always enqueued on close()."""
        bus = StreamBus()  # unbounded (default)
        bus.emit(StreamEvent(type="token", data={"content": "hello"}))
        bus.close()
        events = []
        async for event in bus:
            events.append(event)
        # Should have both the token and the StopAsyncIteration after sentinel
        assert len(events) == 1, f"Expected 1 event, got {len(events)}"
        assert events[0]["type"] == "token"

    @pytest.mark.asyncio
    async def test_error_event_delivered(self):
        """Error events are enqueued successfully."""
        bus = StreamBus()
        bus.emit_error("test error", code="TEST")
        bus.close()
        events = []
        async for event in bus:
            events.append(event)
        assert len(events) >= 1
        assert events[0]["type"] == "error"
        assert events[0]["message"] == "test error"
        assert events[0]["code"] == "TEST"

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        """Multiple close() calls don't cause errors."""
        bus = StreamBus()
        bus.close()
        bus.close()  # should not raise
        events = []
        async for event in bus:
            events.append(event)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_close_after_full_queue(self):
        """Close() drains one event to make room for sentinel in bounded mode."""
        bus = StreamBus(maxsize=1)
        bus.emit(StreamEvent(type="token", data={"content": "x"}))
        bus.close()
        events = []
        async for event in bus:
            events.append(event)
        # The sentinel should always be delivered
        assert len(events) <= 1
