"""Tests for summary-router integration with the broad-recall pipeline."""

from __future__ import annotations

import pytest


class _StubTrace:
    def __init__(self):
        self.spans = []

    def start_span(self, label, kind, **kwargs):
        span = _StubSpan(label, kind, kwargs)
        self.spans.append(span)
        return span

    def finish_span(self, label, status="success", **_kwargs):
        for span in reversed(self.spans):
            if span.label == label and not span.finished:
                span.finished = True
                span.status = status
                break


class _StubSpan:
    def __init__(self, label, kind, kwargs):
        self.label = label
        self.kind = kind
        self.metadata = dict(kwargs)
        self.finished = False
        self.status = None

    def finish(self, status="success", **_kwargs):
        self.finished = True
        self.status = status


@pytest.mark.asyncio
async def test_router_narrows_scope_files(monkeypatch):
    from src.core.config import settings
    from src.services.rag import _routing as _routing_mod

    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK", True)

    routed = [201, 202]

    def _fake_route(query, meeting_ids):
        return list(routed)

    monkeypatch.setattr(
        "src.services.rag._routing.route_files_by_summary",
        _fake_route,
    )

    trace = _StubTrace()
    result = await _routing_mod._route_scope_files_via_summary("alpha topic", [10], trace=trace)
    assert result == routed
    span_labels = [s.label for s in trace.spans]
    assert "summary_router" in span_labels


@pytest.mark.asyncio
async def test_router_disabled_returns_none(monkeypatch):
    from src.core.config import settings
    from src.services.rag import _routing as _routing_mod

    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_ENABLED", False)
    trace = _StubTrace()
    result = await _routing_mod._route_scope_files_via_summary("alpha", [10], trace=trace)
    assert result is None


@pytest.mark.asyncio
async def test_router_empty_falls_through_when_fallback_enabled(monkeypatch):
    from src.core.config import settings
    from src.services.rag import _routing as _routing_mod

    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK", True)

    monkeypatch.setattr(
        "src.services.rag._routing.route_files_by_summary",
        lambda *args, **_kw: [],
    )

    trace = _StubTrace()
    routed = await _routing_mod._route_scope_files_via_summary("alpha", None, trace=trace)
    # Empty list is forwarded to the caller; fallback decision happens at the
    # call site in retrieve_documents (covered in the integration suite).
    assert routed == []


@pytest.mark.asyncio
async def test_router_exception_returns_none(monkeypatch):
    from src.core.config import settings
    from src.services.rag import _routing as _routing_mod

    monkeypatch.setattr(settings, "RAG_SUMMARY_ROUTER_ENABLED", True)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.services.rag._routing.route_files_by_summary", _boom)

    trace = _StubTrace()
    routed = await _routing_mod._route_scope_files_via_summary("alpha", [1], trace=trace)
    assert routed is None


def test_persist_summary_writes_to_vector_collection(monkeypatch):
    """End-to-end persist hook: DB write triggers vector upsert with metadata."""
    from src.services.processor import _pipeline_common

    captured: dict = {}

    def _fake_upsert(file_id, summary, *, meeting_id, file_name=None, file_type=None):
        captured["file_id"] = file_id
        captured["summary"] = summary
        captured["meeting_id"] = meeting_id
        captured["file_name"] = file_name
        captured["file_type"] = file_type

    def _fake_get_meeting_file(_conn, file_id):
        return {
            "id": file_id,
            "meeting_id": 77,
            "file_name": "doc.pdf",
            "file_type": "pdf",
        }

    monkeypatch.setattr(
        "src.services.rag._summary_vectorstore.upsert_file_summary",
        _fake_upsert,
    )
    monkeypatch.setattr(
        "src.core.database.get_meeting_file",
        _fake_get_meeting_file,
    )

    _pipeline_common._sync_file_summary_vector(123, "alpha summary")
    assert captured == {
        "file_id": 123,
        "summary": "alpha summary",
        "meeting_id": 77,
        "file_name": "doc.pdf",
        "file_type": "pdf",
    }


def test_persist_summary_deletes_on_empty(monkeypatch):
    from src.services.processor import _pipeline_common

    deleted: list[int] = []

    monkeypatch.setattr(
        "src.services.rag._summary_vectorstore.delete_file_summary",
        lambda fid: deleted.append(fid),
    )
    # upsert should not be called when summary is empty/whitespace
    monkeypatch.setattr(
        "src.services.rag._summary_vectorstore.upsert_file_summary",
        lambda *a, **kw: pytest.fail("upsert should not be called for empty summary"),
    )

    _pipeline_common._sync_file_summary_vector(55, "   ")
    assert deleted == [55]
