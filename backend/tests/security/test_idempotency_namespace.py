"""Idempotency keys must be isolated and atomically reserve mutations."""

import asyncio

import pytest
from starlette.requests import Request

from src.api.dependencies import IdempotencyGuard


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


@pytest.mark.asyncio
async def test_same_raw_key_is_isolated_by_user_and_path():
    first = IdempotencyGuard("same-key", _request("/first"), "user-a")
    other_user = IdempotencyGuard("same-key", _request("/first"), "user-b")
    other_path = IdempotencyGuard("same-key", _request("/second"), "user-a")

    await first.save({"owner": "a"})
    await other_user.save({"owner": "b"})
    await other_path.save({"owner": "second"})

    assert await first.check() == {"owner": "a"}
    assert await other_user.check() == {"owner": "b"}
    assert await other_path.check() == {"owner": "second"}


@pytest.mark.asyncio
async def test_concurrent_same_key_waits_for_first_result():
    first = IdempotencyGuard("concurrent-key", _request("/atomic"), "user-a")
    second = IdempotencyGuard("concurrent-key", _request("/atomic"), "user-a")
    assert await first.check() is None

    waiter = asyncio.create_task(second.check())
    await asyncio.sleep(0.01)
    assert not waiter.done()
    await first.save({"resource_id": 42})
    assert await waiter == {"resource_id": 42}


@pytest.mark.asyncio
async def test_same_key_with_different_body_is_conflict():
    first = IdempotencyGuard("body-conflict", _request("/atomic"), "user-a", body_hash="a")
    second = IdempotencyGuard("body-conflict", _request("/atomic"), "user-a", body_hash="b")
    assert await first.check() is None
    await first.save({"resource_id": 1})

    with pytest.raises(Exception) as exc_info:
        await second.check()
    assert getattr(exc_info.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_failed_owner_can_release_reservation_for_retry():
    first = IdempotencyGuard("retry-key", _request("/atomic"), "user-a", body_hash="body")
    retry = IdempotencyGuard("retry-key", _request("/atomic"), "user-a", body_hash="body")

    assert await first.check() is None
    await first.abandon()
    assert await retry.check() is None
    await retry.save({"resource_id": 2})
