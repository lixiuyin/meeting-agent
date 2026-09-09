"""Tests for the summary-vector file router."""

from __future__ import annotations

import pytest


class _FakeEmbeddings:
    embedding_dimension = 4

    def embed_documents(self, texts):
        return [self._encode(t) for t in texts]

    def embed_query(self, text):
        return self._encode(text)

    @staticmethod
    def _encode(text: str) -> list[float]:
        text_lower = text.lower()
        return [
            1.0 if "alpha" in text_lower else 0.0,
            1.0 if "beta" in text_lower else 0.0,
            1.0 if "gamma" in text_lower else 0.0,
            float(len(text)) / 1000.0,
        ]


@pytest.fixture
def router_env(monkeypatch, tmp_path):
    from src.core.config import settings
    from src.services.rag import _summary_vectorstore

    fake = _FakeEmbeddings()
    monkeypatch.setattr("src.services.embedder.get_embeddings", lambda: fake)
    monkeypatch.setattr(_summary_vectorstore, "get_embeddings", lambda: fake)
    monkeypatch.setattr(settings, "VECTOR_DB_DIR", tmp_path)
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSION", 4)
    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_TOP_FILES", 5)
    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_MIN_SCORE", 0.0)

    _summary_vectorstore.reset_summary_vectorstore()
    yield _summary_vectorstore
    _summary_vectorstore.reset_summary_vectorstore()


def test_disabled_returns_none(router_env, monkeypatch):
    from src.core.config import settings
    from src.services.rag._summary_router import route_files_by_summary

    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_ENABLED", False)
    assert route_files_by_summary("alpha topic", [1]) is None


def test_empty_collection_returns_none(router_env):
    from src.services.rag._summary_router import route_files_by_summary

    assert route_files_by_summary("alpha topic", [1]) is None


def test_routes_relevant_files(router_env):
    mod = router_env
    mod.upsert_file_summary(101, "alpha planning notes", meeting_id=10)
    mod.upsert_file_summary(102, "beta budget review", meeting_id=10)
    mod.upsert_file_summary(103, "gamma postmortem", meeting_id=10)

    from src.services.rag._summary_router import route_files_by_summary

    selected = route_files_by_summary("alpha", meeting_ids=[10], top_k=2)
    assert selected is not None
    assert 101 in selected
    # alpha-only file outranks unrelated ones
    assert selected[0] == 101


def test_meeting_filter_excludes_other_meetings(router_env):
    mod = router_env
    mod.upsert_file_summary(201, "alpha alpha alpha", meeting_id=20)
    mod.upsert_file_summary(202, "alpha alpha alpha", meeting_id=21)

    from src.services.rag._summary_router import route_files_by_summary

    selected = route_files_by_summary("alpha", meeting_ids=[20])
    assert selected is not None
    assert 201 in selected
    assert 202 not in selected


def test_user_filter_excludes_other_principals(router_env):
    mod = router_env
    mod.upsert_file_summary(
        211,
        "alpha alpha",
        meeting_id=20,
        extra_metadata={"user_id": "principal-a"},
    )
    mod.upsert_file_summary(
        212,
        "alpha alpha",
        meeting_id=21,
        extra_metadata={"user_id": "principal-b"},
    )

    from src.services.rag._summary_router import route_files_by_summary

    selected = route_files_by_summary("alpha", user_id="principal-a")
    assert selected is not None
    assert 211 in selected
    assert 212 not in selected


def test_empty_query_returns_none(router_env):
    from src.services.rag._summary_router import route_files_by_summary

    assert route_files_by_summary("   ", meeting_ids=None) is None


def test_search_failure_returns_none(router_env, monkeypatch):
    mod = router_env
    mod.upsert_file_summary(301, "alpha", meeting_id=30)

    from src.services.rag import _summary_router

    class _BoomStore:
        @property
        def _collection(self):
            class _C:
                def count(self):
                    return 1

            return _C()

        def similarity_search_with_score(self, *_args, **_kwargs):
            raise RuntimeError("backend exploded")

    monkeypatch.setattr(_summary_router, "get_summary_vectorstore", lambda: _BoomStore())
    # On search failure the router returns an empty list (graceful degradation, not None).
    result = _summary_router.route_files_by_summary("alpha", [30])
    assert not result


def test_top_files_caps_result(router_env):
    mod = router_env
    for fid in range(1, 11):
        mod.upsert_file_summary(fid, "alpha", meeting_id=99)

    from src.services.rag._summary_router import route_files_by_summary

    selected = route_files_by_summary("alpha", meeting_ids=[99], top_k=3)
    assert selected is not None
    assert len(selected) == 3
