"""Fault boundaries discovered during the industrial-readiness audit."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from src.core import database as db
from src.core.memory_policy import is_active_memory


@pytest.mark.parametrize(
    "metadata",
    [
        {"expires_at": "2000-01-01"},
        {"valid_to": "2000-01-01"},
        {"valid_from": "2999-01-01"},
        {"superseded_by": "replacement"},
        {"valid_to": "malformed"},
    ],
)
def test_inactive_memory_is_never_a_current_fact(metadata):
    assert not is_active_memory(metadata)


@pytest.mark.asyncio
async def test_search_filters_before_expansion_and_reads_sql_content(monkeypatch):
    from src.core.config import settings
    from src.services.memory import MemoryService
    from src.services.memory._service import _search

    vs = MagicMock()
    vs.similarity_search.return_value = [
        {"key": k, "content": "private stale vector content", "score": 0.0}
        for k in ("expired", "superseded", "outside", "active")
    ]
    rows = {
        "expired": {"value": "expired", "expires_at": "2000-01-01", "meeting_ids": "1"},
        "superseded": {"value": "superseded", "superseded_by": "active", "meeting_ids": "1"},
        "outside": {"value": "outside", "meeting_ids": "2"},
        "active": {"value": "current", "meeting_ids": "1"},
    }
    monkeypatch.setattr(_search, "get_memory_vectorstore", lambda: vs)
    monkeypatch.setattr(_search.db, "get_memories_batch", lambda *a, **kw: rows)
    monkeypatch.setattr(_search.db, "list_memory_keys_for_scope", lambda *a, **kw: ["active"])
    service = MemoryService()
    monkeypatch.setattr(service, "search_important", lambda *a, **kw: [])
    monkeypatch.setattr(settings, "MEMORY_MULTI_HOP_ENABLED", True)
    result = await service.search_semantic("user", "private", meeting_ids=[1])
    assert [r.key for r in result] == ["active"]
    assert result[0].value == "current"
    assert vs.similarity_search.call_count == 1


def test_changed_owner_cannot_be_fuzzy_deduplicated():
    from src.services.chain._retrieve_post import _near_duplicate

    prefix = (
        "The delivery review confirmed the release checklist and all outstanding action items. "
    )
    assert not _near_duplicate(
        prefix + "Owner is Alice.", prefix + "Owner is Bob.", threshold=0.85, n=4
    )


@pytest.mark.asyncio
async def test_ambiguous_commit_cannot_be_claimed_again():
    from src.api.dependencies import IdempotencyGuard

    request = Request({"type": "http", "method": "GET", "path": "/fault", "headers": []})
    guard = IdempotencyGuard("fault", request, "u")
    assert await guard.check() is None

    def mutate():
        with db.get_write_connection() as conn:
            conn.execute("INSERT INTO kv_state(key, value) VALUES ('fault-probe', 'committed')")

    await asyncio.to_thread(mutate)
    await guard.abandon()
    with db.get_write_connection() as conn:
        conn.execute(
            "UPDATE idempotency_keys SET expires_at='2000-01-01' WHERE key=?", (guard._storage_key,)
        )
        state, _, _ = db.claim_idempotency_request(
            conn, key=guard._storage_key, method="GET", path="/fault", user_id="u", body_hash=None
        )
    assert state == "recovery_required"


def test_history_snapshot_cannot_replace_new_sql_or_resurrect_deleted(monkeypatch, tmp_path):
    from src.services.memory import _history

    with db.get_write_connection() as conn:
        sid = db.create_session(conn, user_id="cache-test")
        db.add_message(conn, session_id=sid, role="human", content="new SQL truth")
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({sid: [{"role": "human", "content": "old cache"}], "deleted": []}))
    monkeypatch.setattr(_history, "_SESSION_CACHE_PATH", path)
    _history._load_session_cache()
    assert _history.get_session_history(sid).messages[0].content == "new SQL truth"
    assert "deleted" not in _history._histories


def test_baseline_all_fails_preflight_without_running_providers(monkeypatch):
    from scripts import benchmark

    monkeypatch.setattr(
        "sys.argv", ["benchmark", "all", "--baseline", "--process-report", "unused.json"]
    )
    monkeypatch.setattr(benchmark, "_load_baseline", lambda: {"payloads": [{"command": "memory"}]})
    runner = MagicMock(side_effect=AssertionError("provider must not run"))
    monkeypatch.setattr(benchmark, "run_chat_benchmark", runner)
    assert benchmark.main() == 2
    runner.assert_not_called()


def test_baseline_compares_changed_implementation():
    from scripts.benchmark import _compare_baseline

    base = {
        "stats": {"recall": 0.8},
        "run_metadata": {
            "dataset_fingerprint_sha256": "a",
            "harness_fingerprint_sha256": "b",
            "implementation_fingerprint_sha256": "old",
        },
    }
    current = {
        **base,
        "run_metadata": {**base["run_metadata"], "implementation_fingerprint_sha256": "new"},
    }
    assert _compare_baseline(current, base, 0.1) == []
