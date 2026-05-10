"""Shared HTTP client for vision model calls."""

from __future__ import annotations

import httpx

from ...core.http_client import LoopBoundAsyncClient

_vision_client = LoopBoundAsyncClient(lambda: httpx.AsyncClient(timeout=120))


def get_vision_client() -> httpx.AsyncClient:
    """Get or create a shared async HTTP client for vision requests."""
    return _vision_client.get()


async def close_vision_client() -> None:
    """Close shared vision client."""
    await _vision_client.close()
