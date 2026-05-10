import pytest

from src.services.chain import _retrieve_broad as retrieve_broad
from src.services.chain import _steps_retrieve as retrieve_steps
from src.services.chain._context import PipelineContext


@pytest.mark.anyio
async def test_retrieve_documents_offloads_single_query_to_thread(monkeypatch):
    """Scoped path (file_ids set) offloads retrieve_fn to a thread."""
    monkeypatch.setattr(retrieve_steps.settings, "MULTI_QUERY_ENABLED", False)
    monkeypatch.setattr(retrieve_steps.settings, "HYBRID_SEARCH_ENABLED", False)
    monkeypatch.setattr(retrieve_steps.settings, "RERANKER_BINDING", "")

    monkeypatch.setattr(
        retrieve_steps,
        "determine_adaptive_top_k",
        lambda _q, _k, *, is_broad_recall=False: 5,
    )
    monkeypatch.setattr(retrieve_steps, "retrieve_sibling_chunks", lambda *_args, **_kwargs: [])

    calls: list[str] = []

    def _fake_retrieve(*_args, **_kwargs):
        calls.append("retrieve")
        return ([{"content": "hit", "metadata": {"meeting_id": 1}, "score": 0.9}], None)

    async def _fake_to_thread(func, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(retrieve_broad, "retrieve", _fake_retrieve)
    monkeypatch.setattr(retrieve_steps.asyncio, "to_thread", _fake_to_thread)

    ctx = PipelineContext(question="What did they discuss?", meeting_ids=[1], file_ids=[9], top_k=5)
    await retrieve_steps.retrieve_documents(ctx)

    assert "_fake_retrieve" in calls
    assert calls.count("retrieve") == 1
    assert len(ctx.docs) == 1


@pytest.mark.anyio
async def test_retrieve_documents_uses_scoped_file_k_floor(monkeypatch):
    monkeypatch.setattr(retrieve_steps.settings, "MULTI_QUERY_ENABLED", False)
    monkeypatch.setattr(retrieve_steps.settings, "HYBRID_SEARCH_ENABLED", False)
    monkeypatch.setattr(retrieve_steps.settings, "RERANKER_BINDING", "")

    monkeypatch.setattr(
        retrieve_steps,
        "determine_adaptive_top_k",
        lambda _q, _k, *, is_broad_recall=False: 3,
    )
    monkeypatch.setattr(retrieve_steps, "retrieve_sibling_chunks", lambda *_args, **_kwargs: [])

    captured_top_k: list[int] = []

    def _fake_retrieve(_query, _meeting_ids, _file_ids, top_k, *_args, **_kwargs):
        captured_top_k.append(top_k)
        return (
            [{"content": "hit", "metadata": {"meeting_id": 1, "file_id": 9}, "score": 0.9}],
            None,
        )

    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(retrieve_broad, "retrieve", _fake_retrieve)
    monkeypatch.setattr(retrieve_steps.asyncio, "to_thread", _fake_to_thread)

    ctx = PipelineContext(question="q", meeting_ids=[1], file_ids=[9], top_k=5)
    await retrieve_steps.retrieve_documents(ctx)

    assert captured_top_k == [12]
