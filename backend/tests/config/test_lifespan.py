import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.api.lifespan import _startup_summary_backfill_enabled
from src.services.parser._http import (
    close_parser_http_client,
    get_parser_http_client,
)


def test_startup_backfill_disabled_by_default(monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "SESSION_SUMMARY_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SUMMARY_STARTUP_BACKFILL", False)
    assert _startup_summary_backfill_enabled() is False


# ── Lifespan shutdown cleanliness ────────────────────────────────────────────


@pytest.mark.anyio
async def test_parser_shutdown_after_threadpool_no_warnings(caplog):
    """Simulate the ThreadPoolExecutor + asyncio.run pattern followed by
    lifespan shutdown — no 'Event loop is closed' warnings."""

    # 1. Create a client in a worker thread (simulating cascade dispatch)
    def worker() -> None:
        async def inner() -> None:
            get_parser_http_client()

        asyncio.run(inner())

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as ex:
        await loop.run_in_executor(ex, worker)
    # Worker loop is now closed, client may linger

    # 2. Simulate lifespan shutdown calling close_parser_http_client
    caplog.set_level(logging.WARNING, logger="src.services.parser._http")
    await close_parser_http_client()

    assert not any("Event loop is closed" in r.message for r in caplog.records), (
        f"Unexpected warnings: {[r.message for r in caplog.records]}"
    )
