"""Test httpx client recovery when the bound event loop is closed."""

import asyncio

import pytest

from src.services.parser._http import get_parser_http_client


async def _get_client():
    return get_parser_http_client()


def test_per_loop_client_isolation():
    """Each asyncio.run loop gets its own client; they never interfere."""
    loop_a = asyncio.new_event_loop()
    client_a = loop_a.run_until_complete(_get_client())
    loop_a.close()

    loop_b = asyncio.new_event_loop()
    client_b = loop_b.run_until_complete(_get_client())
    loop_b.close()

    assert client_a is not client_b


def test_same_loop_reuses_client():
    """Multiple calls on the same loop return the same client instance."""
    loop = asyncio.new_event_loop()
    c1 = loop.run_until_complete(_get_client())
    c2 = loop.run_until_complete(_get_client())
    loop.close()

    assert c1 is c2


def test_raises_outside_loop():
    """Calling outside any event loop should raise RuntimeError."""
    # Ensure we're not inside a loop
    assert asyncio._get_running_loop() is None
    with pytest.raises(RuntimeError, match="running event loop"):
        get_parser_http_client()
