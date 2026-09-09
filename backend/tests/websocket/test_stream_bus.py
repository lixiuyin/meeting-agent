import asyncio

import pytest

from src.services.stream_bus import StreamBus


@pytest.mark.anyio
async def test_close_does_not_raise_when_queue_full() -> None:
    bus = StreamBus(maxsize=1)
    bus.emit_token("x")
    bus.close()


@pytest.mark.anyio
async def test_consumer_gets_sentinel_under_backpressure() -> None:
    bus = StreamBus(maxsize=4000)
    token_count = 2000
    for i in range(token_count):
        bus.emit_token(str(i))
    bus.close()

    consumed = 0

    async def _consume() -> None:
        nonlocal consumed
        async for _event in bus:
            consumed += 1

    await asyncio.wait_for(_consume(), timeout=2.0)
    assert consumed == token_count


@pytest.mark.anyio
async def test_heartbeat_wakes_blocked_consumer() -> None:
    """Regression: emit_heartbeat must wake a consumer awaiting on an empty
    queue. Previously it only set a flag, leaving the consumer blocked on
    asyncio.Queue.get() during quiet pipeline phases (retrieval/rerank/etc.),
    which surfaced as the frontend "Stream stalled" error after 30s.
    """
    bus = StreamBus()
    received: list[dict] = []

    async def _consume() -> None:
        async for event in bus:
            received.append(event)

    consumer = asyncio.create_task(_consume())
    # Let the consumer enter its blocking await on queue.get().
    await asyncio.sleep(0)

    bus.emit_heartbeat()
    # The heartbeat must arrive promptly; we deliberately use a tight bound
    # so a regression to the flag-only design times out instead of hanging.
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)

    assert received, "heartbeat did not wake the blocked consumer"
    assert received[0]["type"] == "heartbeat"

    bus.emit_done("session-123")
    await asyncio.wait_for(consumer, timeout=1.0)


@pytest.mark.anyio
async def test_heartbeat_dropped_when_queue_full() -> None:
    """When the queue is full of token events, the frontend stall timer is
    being reset by those tokens anyway, so dropping a heartbeat is safe and
    must not block the producer.
    """
    bus = StreamBus(maxsize=2)
    bus.emit_token("a")
    bus.emit_token("b")
    # Should not raise nor block.
    bus.emit_heartbeat()


@pytest.mark.anyio
async def test_done_can_publish_persisted_message_ids() -> None:
    bus = StreamBus()
    bus.emit_done("session-123", message_ids=[17, 18])
    events = [event async for event in bus]
    assert events == [
        {
            "type": "done",
            "seq": 0,
            "session_id": "session-123",
            "message_ids": [17, 18],
        }
    ]


@pytest.mark.anyio
async def test_overflow_tokens_never_get_overtaken() -> None:
    """A later token must not enter a newly freed slot ahead of overflow."""
    bus = StreamBus(maxsize=1)
    bus.emit_token("A")
    bus.emit_token("B")

    first = await anext(bus)
    bus.emit_token("C")
    bus.emit_done("session-123")
    remaining = [event async for event in bus]

    events = [first, *remaining]
    assert "".join(event.get("content", "") for event in events) == "ABC"
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)


@pytest.mark.anyio
async def test_critical_events_keep_order_while_overflowing() -> None:
    bus = StreamBus(maxsize=1)
    bus.emit_token("A")
    bus.emit_token("B")
    bus.emit_sources([{"id": 1}])
    bus.emit_token("C")
    bus.emit_done("session-123")

    events = [event async for event in bus]
    assert [event["type"] for event in events] == ["token", "token", "sources", "token", "done"]
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
    assert "".join(event.get("content", "") for event in events) == "ABC"


@pytest.mark.anyio
async def test_slow_client_buffer_has_explicit_byte_limit() -> None:
    bus = StreamBus(maxsize=1, max_buffer_bytes=3)
    bus.emit_token("A")
    bus.emit_token("B")
    bus.emit_token("C")
    bus.emit_token("D")

    events = [event async for event in bus]
    assert "".join(event.get("content", "") for event in events) == "ABC"
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "STREAM_BACKPRESSURE_LIMIT"
    assert bus._buffered_bytes == 0


@pytest.mark.anyio
async def test_worker_thread_emit_wakes_async_consumer() -> None:
    bus = StreamBus()
    consumer = asyncio.create_task(anext(bus))
    await asyncio.sleep(0)

    await asyncio.to_thread(bus.emit_token, "thread-token")
    event = await asyncio.wait_for(consumer, timeout=1)
    assert event["content"] == "thread-token"

    await asyncio.to_thread(bus.emit_done, "session-123")
    terminal = await asyncio.wait_for(anext(bus), timeout=1)
    assert terminal["type"] == "done"
