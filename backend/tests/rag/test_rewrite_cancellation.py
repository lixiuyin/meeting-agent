import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.rag import _query


@pytest.mark.asyncio
async def test_rewrite_timeout_cancels_provider_and_preserves_original(monkeypatch):
    cancelled = asyncio.Event()

    async def invoke(_messages):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    model = SimpleNamespace(ainvoke=invoke)
    monkeypatch.setattr(_query, "_get_rewrite_llm", lambda: model)
    monkeypatch.setattr(_query.settings, "QUERY_REWRITE_TIMEOUT_SECONDS", 0.02)
    _query._clear_rewrite_cache()
    question = "Explain why the integration rollout changed after the latest review"
    assert await _query.rewrite_query(question) == question
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_successful_async_rewrite_is_cached(monkeypatch):
    invoke = AsyncMock(return_value=SimpleNamespace(content="integration rollout constraints"))
    monkeypatch.setattr(_query, "_get_rewrite_llm", lambda: SimpleNamespace(ainvoke=invoke))
    _query._clear_rewrite_cache()
    question = "Explain the integration rollout constraints discussed during the review"
    assert await _query.rewrite_query(question) == "integration rollout constraints"
    assert await _query.rewrite_query(question) == "integration rollout constraints"
    assert invoke.await_count == 1
