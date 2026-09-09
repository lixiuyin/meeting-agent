import asyncio

import httpx
import pytest

from src.core.http_client import LoopBoundAsyncClient


@pytest.mark.asyncio
async def test_loop_bound_client_recreates_when_config_key_changes() -> None:
    state = {"timeout": 1.0}
    clients: list[httpx.AsyncClient] = []

    def factory() -> httpx.AsyncClient:
        client = httpx.AsyncClient(timeout=state["timeout"])
        clients.append(client)
        return client

    holder = LoopBoundAsyncClient(factory, key_factory=lambda: state["timeout"])
    first = holder.get()
    assert holder.get() is first

    state["timeout"] = 2.0
    second = holder.get()
    await asyncio.sleep(0)

    assert second is not first
    assert first.is_closed
    await holder.close()
