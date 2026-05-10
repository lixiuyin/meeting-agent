"""Tests for async-generator cleanup on client/stream disconnect.

Verifies that inner llm.astream() generators are properly aclose()'d when a
consumer disconnects mid-stream, preventing ``RuntimeError('async generator
ignored GeneratorExit')`` from surfacing in logs.
"""

import asyncio
import gc
import logging
from types import SimpleNamespace

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakeLLM:
    """LLM stub whose astream yields tokens slowly then blocks forever."""

    def __init__(self, token_count: int = 5, delay: float = 0.05):
        self._token_count = token_count
        self._delay = delay

    async def astream(self, _prompt):
        for i in range(self._token_count):
            yield SimpleNamespace(content=f"token{i}")
            await asyncio.sleep(self._delay)
        # Block forever after yielding all tokens — simulates an open stream
        await asyncio.sleep(600)

    async def ainvoke(self, _prompt):
        return SimpleNamespace(content="fallback")


class _FakeMatcher:
    async def match(self, _question, _skills):
        return None


async def _noop_async(_ctx):
    return None


def _noop_sync(_ctx):
    return None


def _patch_chain(monkeypatch, fake_llm):
    """Monkey-patch chain pipeline steps so only the LLM stream is live."""
    from src.services.chain import _api as chain_api

    monkeypatch.setattr(chain_api, "_classify_intent", lambda _q: "rag")
    monkeypatch.setattr(
        chain_api,
        "_get_skill_loader",
        lambda: SimpleNamespace(load_all=lambda: []),
    )
    monkeypatch.setattr(chain_api, "_get_skill_matcher", lambda: _FakeMatcher())
    monkeypatch.setattr(chain_api, "ensure_session", lambda ctx: setattr(ctx, "session_id", "s1"))
    monkeypatch.setattr(chain_api, "rewrite_query_step", _noop_async)
    monkeypatch.setattr(chain_api, "retrieve_documents", _noop_async)
    monkeypatch.setattr(chain_api, "rerank_documents", _noop_sync)
    monkeypatch.setattr(chain_api, "suppress_near_duplicates", _noop_sync)
    monkeypatch.setattr(chain_api, "load_memories", _noop_async)
    monkeypatch.setattr(chain_api, "load_session_context", _noop_async)
    monkeypatch.setattr(chain_api, "load_entity_context", _noop_async)
    monkeypatch.setattr(chain_api, "perform_web_search", _noop_async)
    monkeypatch.setattr(chain_api, "load_history", _noop_async)
    monkeypatch.setattr(chain_api, "build_context", _noop_sync)
    monkeypatch.setattr(chain_api, "save_messages", _noop_sync)
    monkeypatch.setattr(chain_api, "schedule_fact_extraction", _noop_sync)

    class _Prompt:
        def __or__(self, _other):
            return fake_llm

    monkeypatch.setattr("src.services.llm.get_llm", lambda: fake_llm)
    monkeypatch.setattr("src.services.llm.get_rag_prompt", lambda: _Prompt())


# ── Chat stream disconnect tests ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_chat_stream_aclose_no_generator_exit_error(monkeypatch, caplog):
    """Closing the ask_stream generator mid-stream must not log GeneratorExit errors."""
    from src.services.chain import _api as chain_api

    fake_llm = _FakeLLM(token_count=3, delay=0.03)
    _patch_chain(monkeypatch, fake_llm)

    caplog.set_level(logging.ERROR, logger="asyncio")

    gen = chain_api.ask_stream("disconnect test")
    # Consume a couple of tokens then abandon
    count = 0
    async for _event in gen:
        count += 1
        if count >= 3:
            break

    await gen.aclose()

    # Force GC to flush any dangling generators
    gc.collect()
    await asyncio.sleep(0.1)

    assert not any("async generator ignored GeneratorExit" in r.message for r in caplog.records), (
        f"Unexpected GeneratorExit errors: {[r.message for r in caplog.records]}"
    )


# ── Summary stream disconnect tests ──────────────────────────────────────────


@pytest.mark.anyio
async def test_summary_stream_closes_inner_astream(monkeypatch, caplog):
    """Summary endpoint: inner llm.astream() must be aclose'd on consumer disconnect."""
    from src.api.routers.meetings._summary import generate_summary_stream
    from src.core import database as db
    from src.models.schemas import MeetingStatus

    fake_llm = _FakeLLM(token_count=3, delay=0.03)
    monkeypatch.setattr("src.services.llm.get_llm", lambda: fake_llm)
    monkeypatch.setattr(
        "src.services.tokenizer.count_tokens",
        lambda text, model="": max(1, len(text.split())),
    )

    # Insert a meeting with a ready file
    with db.get_write_connection() as conn:
        conn.execute(
            "INSERT INTO meetings (id, title, status) VALUES (?, ?, ?)",
            (9001, "Test Meeting", MeetingStatus.READY),
        )
        conn.execute(
            "INSERT INTO meeting_files (id, meeting_id, file_name, file_type, file_path, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (8001, 9001, "test.pdf", "pdf", "/tmp/test.pdf", MeetingStatus.READY),
        )
        conn.execute(
            "UPDATE meeting_files SET transcript = ? WHERE id = ?",
            ("This is a test transcript " * 50, 8001),
        )
        conn.commit()

    caplog.set_level(logging.ERROR, logger="asyncio")

    response = await generate_summary_stream(9001, principal={"user_id": "default"})
    # response is a StreamingResponse — iterate its generator
    gen = response.body_iterator
    count = 0
    async for _chunk in gen:
        count += 1
        if count >= 3:
            break

    # Close the generator to simulate disconnect
    if hasattr(gen, "aclose"):
        await gen.aclose()

    gc.collect()
    await asyncio.sleep(0.1)

    assert not any("async generator ignored GeneratorExit" in r.message for r in caplog.records), (
        f"Unexpected GeneratorExit errors: {[r.message for r in caplog.records]}"
    )
