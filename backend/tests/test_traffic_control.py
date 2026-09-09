"""Regression tests for traffic-controller lifecycle and breaker recovery."""

import asyncio

import pytest

from src.services.traffic_control import CircuitBreaker, TrafficController


def test_half_open_allows_required_serial_recovery_probes(monkeypatch):
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=1, recovery_successes=2)
    cb.record_failure()
    monkeypatch.setattr(
        "src.services.traffic_control.time.monotonic",
        lambda: cb._last_failure_time + 2,
    )

    assert cb.is_call_allowed() is True
    cb.record_success()
    assert cb.is_call_allowed() is True
    cb.record_success()
    assert cb.state == "closed"


@pytest.mark.asyncio
async def test_cancelled_token_waiter_releases_semaphore():
    controller = TrafficController(max_concurrency=1, rpm=1, timeout=1)
    controller._tokens = 0.0

    async def wait_for_token() -> None:
        async with controller:
            pass

    task = asyncio.create_task(wait_for_token())
    await asyncio.sleep(0.02)
    assert controller._semaphore._value == 0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert controller._semaphore._value == 1


@pytest.mark.asyncio
async def test_cancelled_half_open_probe_can_be_retried():
    breaker = CircuitBreaker()
    breaker._state = "half-open"
    controller = TrafficController(max_concurrency=1, rpm=1, circuit_breaker=breaker)
    controller._tokens = 0.0

    task = asyncio.create_task(controller.__aenter__())
    await asyncio.sleep(0.02)
    assert breaker.state == "half-open"  # externally probing
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert breaker._state == "half-open"
    assert controller._semaphore._value == 1


@pytest.mark.asyncio
async def test_timed_out_half_open_probe_can_be_retried():
    breaker = CircuitBreaker()
    breaker._state = "half-open"
    controller = TrafficController(
        max_concurrency=1,
        timeout=0.01,
        circuit_breaker=breaker,
    )
    await controller._semaphore.acquire()

    with pytest.raises(TimeoutError):
        await controller.__aenter__()

    assert breaker._state == "half-open"
    controller._semaphore.release()


@pytest.mark.asyncio
async def test_context_records_one_failure():
    controller = TrafficController()
    with pytest.raises(RuntimeError):
        async with controller:
            raise RuntimeError("provider failed")
    assert controller.breaker._failure_count == 1


def test_stream_pipeline_uses_current_controller(monkeypatch):
    from src.services import traffic_control
    from src.services.chain import _api_stream

    controller = TrafficController()
    monkeypatch.setattr(traffic_control, "traffic_controller", controller)
    assert _api_stream.get_traffic_controller() is controller


def test_init_uses_breaker_settings(monkeypatch):
    from src.core.config import settings
    from src.core.metrics import BREAKER_STATE, TRAFFIC_INFLIGHT
    from src.services import traffic_control

    monkeypatch.setattr(settings, "LLM_CIRCUIT_BREAKER_THRESHOLD", 9)
    monkeypatch.setattr(settings, "LLM_CIRCUIT_BREAKER_RECOVERY", 17)
    controller = traffic_control.init_traffic_controller()
    assert controller.breaker._failure_threshold == 9
    assert controller.breaker._recovery_timeout == 17
    assert BREAKER_STATE._value.get() == 1.0
    assert TRAFFIC_INFLIGHT._value.get() == 0.0
