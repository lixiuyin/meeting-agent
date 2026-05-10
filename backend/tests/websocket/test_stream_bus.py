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
