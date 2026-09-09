import datetime

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


@pytest.mark.anyio
async def test_natural_language_historical_cutoff_reaches_rag_retriever(monkeypatch):
    monkeypatch.setattr(retrieve_steps.settings, "MULTI_QUERY_ENABLED", False)
    monkeypatch.setattr(retrieve_steps.settings, "HYBRID_SEARCH_ENABLED", False)
    monkeypatch.setattr(retrieve_steps.settings, "RERANKER_BINDING", "")
    monkeypatch.setattr(
        retrieve_steps,
        "determine_adaptive_top_k",
        lambda _q, _k, *, is_broad_recall=False: 5,
    )
    monkeypatch.setattr(retrieve_steps, "retrieve_sibling_chunks", lambda *_args, **_kwargs: [])
    observed_date_to: list[datetime.date | None] = []

    def _fake_retrieve(
        _query,
        _meeting_ids,
        _file_ids,
        _top_k,
        _fetch_multiplier,
        _file_types,
        _date_from,
        date_to,
        *_args,
        **_kwargs,
    ):
        observed_date_to.append(date_to)
        return ([{"content": "hit", "metadata": {"file_id": 9}, "score": 0.9}], None)

    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(retrieve_broad, "retrieve", _fake_retrieve)
    monkeypatch.setattr(retrieve_steps.asyncio, "to_thread", _fake_to_thread)

    ctx = PipelineContext(
        question="截至2025年3月1日, Orbit的负责人是谁?",
        file_ids=[9],
        top_k=5,
    )
    await retrieve_steps.retrieve_documents(ctx)

    assert ctx.date_to == datetime.date(2025, 3, 1)
    assert observed_date_to == [datetime.date(2025, 3, 1)]


@pytest.mark.anyio
async def test_scoped_multi_query_merges_public_relevance_scores() -> None:
    """Hybrid relevance results must not be reversed by vector metric settings."""
    calls = 0

    def _fake_retrieve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        score = 0.2 if calls == 1 else 0.9
        return (
            [
                {
                    "content": "same chunk",
                    "metadata": {"meeting_id": 1, "file_id": 9},
                    "score": score,
                    "score_kind": "relevance",
                }
            ],
            None,
        )

    ctx = PipelineContext(question="q", meeting_ids=[1], file_ids=[9], top_k=5)
    docs, _ = await retrieve_broad._retrieve_scoped(
        ctx,
        ["variant one", "variant two"],
        effective_k=5,
        fetch_multiplier=1,
        known_speakers=[],
        retrieve_fn=_fake_retrieve,
    )

    assert docs[0]["score"] == 0.9
    assert docs[0]["score_kind"] == "relevance"


@pytest.mark.anyio
async def test_speaker_and_temporal_filters_are_composed(monkeypatch):
    from src.services.rag._query_analysis import QueryAnalysis, TemporalHint

    qa = QueryAnalysis(
        speaker_names=["Alex"],
        temporal_hint=TemporalHint(ratio_min=0.0, ratio_max=0.25),
    )
    docs = [{"content": "hit", "metadata": {"meeting_id": 1, "file_id": 9}, "score": 0.9}]

    async def fake_scoped(*_args, **_kwargs):
        return docs, qa

    calls: list[str] = []
    monkeypatch.setattr(retrieve_steps, "_retrieve_scoped", fake_scoped)
    monkeypatch.setattr(
        retrieve_steps,
        "_apply_speaker_filter",
        lambda values, *_args: calls.append("speaker") or values,
    )
    monkeypatch.setattr(
        retrieve_steps,
        "_apply_temporal_filter",
        lambda values, *_args: calls.append("temporal") or values,
    )
    monkeypatch.setattr(retrieve_steps.settings, "MULTI_QUERY_ENABLED", False)
    monkeypatch.setattr(retrieve_steps.settings, "RAG_SIBLING_CORETRIEVE_ENABLED", False)

    ctx = PipelineContext(question="What did Alex say at the start?", file_ids=[9])
    await retrieve_steps.retrieve_documents(ctx)

    assert calls == ["speaker", "temporal"]
