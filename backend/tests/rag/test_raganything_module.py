"""Unit tests for RAGAnything helper module."""

import pytest

from src.services.rag import _raganything as raganything_module


def test_run_async_without_running_loop():
    async def _value() -> int:
        return 42

    assert raganything_module._run_async(_value()) == 42


@pytest.mark.anyio
async def test_run_async_with_running_loop():
    async def _value() -> str:
        return "ok"

    assert raganything_module._run_async(_value()) == "ok"


def test_ensure_operation_succeeded_rejects_explicit_failure():
    with pytest.raises(RuntimeError, match="index failed: timeout"):
        raganything_module._ensure_operation_succeeded(
            {"success": False, "error": "timeout"},
            operation="index",
        )


def test_ensure_operation_succeeded_accepts_success_payload():
    payload = {"status": "success", "data": []}
    assert raganything_module._ensure_operation_succeeded(payload, operation="query") == payload


def test_index_with_raganything_carries_scope_preamble(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeRag:
        async def insert_content_list(self, *, content_list, **kwargs):
            captured.update({"content_list": content_list, **kwargs})
            return {"status": "success"}

    monkeypatch.setattr(raganything_module, "_get_raganything", lambda: _FakeRag())

    raganything_module.index_with_raganything(
        meeting_id=10,
        file_id=3,
        text="Quarterly roadmap content",
        metadata={"title": "Q1 Plan"},
    )

    content_list = captured.get("content_list")
    assert isinstance(content_list, list)
    assert len(content_list) == 1
    text_payload = content_list[0].get("text")
    assert isinstance(text_payload, str)
    assert text_payload.startswith("[SCOPE meeting_id=10 file_id=3 doc_id=meeting_10_file_3]")
    assert captured.get("doc_id") == "meeting_10_file_3"


def test_extract_doc_id_from_preamble():
    content = "[SCOPE meeting_id=2 file_id=8 doc_id=meeting_2_file_8]\n\nchunk"
    doc_id = raganything_module._extract_doc_id({"content": content, "metadata": {}})
    assert doc_id == "meeting_2_file_8"


def test_extract_doc_id_from_metadata():
    doc_id = raganything_module._extract_doc_id(
        {"content": "chunk", "metadata": {"doc_id": "meeting_1_file_4"}}
    )
    assert doc_id == "meeting_1_file_4"


def test_scope_match_accepts_matching_meeting_and_file():
    assert raganything_module._scope_match("meeting_7_file_11", {7}, {11}) is True


def test_scope_match_drops_non_matching_doc_id():
    assert raganything_module._scope_match("meeting_7_file_12", {7}, {11}) is False


def test_scope_match_via_db_sidecar_when_doc_id_nonstandard(monkeypatch):
    monkeypatch.setattr(raganything_module, "_lookup_scope_ids_by_doc_id", lambda _doc: (5, 9))
    assert raganything_module._scope_match("opaque-doc-id", {5}, {9}) is True
    assert raganything_module._scope_match("opaque-doc-id", {6}, {9}) is False


def test_retrieve_with_raganything_empty_payload_returns_empty_list(monkeypatch):
    class _FakeRag:
        async def aquery(self, *_args, **_kwargs):
            return {"status": "success", "results": []}

    monkeypatch.setattr(raganything_module, "_get_raganything", lambda: _FakeRag())
    monkeypatch.setattr(raganything_module, "_import_raganything_types", lambda: (object(), None))

    out = raganything_module.retrieve_with_raganything("hello", top_k=5, filters={})
    assert out == []


def test_retrieve_with_raganything_post_filters_by_scope(monkeypatch):
    class _FakeRag:
        async def aquery(self, *_args, **_kwargs):
            return [
                {
                    "content": "[SCOPE meeting_id=1 file_id=2 doc_id=meeting_1_file_2]\n\nA",
                    "metadata": {},
                    "score": 0.1,
                },
                {
                    "content": "[SCOPE meeting_id=3 file_id=4 doc_id=meeting_3_file_4]\n\nB",
                    "metadata": {},
                    "score": 0.2,
                },
            ]

    monkeypatch.setattr(raganything_module, "_get_raganything", lambda: _FakeRag())
    monkeypatch.setattr(raganything_module, "_import_raganything_types", lambda: (object(), None))

    out = raganything_module.retrieve_with_raganything(
        "hello",
        top_k=5,
        filters={"$and": [{"meeting_id": {"$in": [1]}}, {"file_id": {"$in": [2]}}]},
    )
    assert len(out) == 1
    assert "meeting_1_file_2" in out[0]["content"]


def test_reset_raganything_clears_singleton():
    raganything_module._raganything_singleton = object()
    raganything_module.reset_raganything()
    assert raganything_module._raganything_singleton is None
