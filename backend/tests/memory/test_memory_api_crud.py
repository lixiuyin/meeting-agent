"""Tests for memory API CRUD, validation, update, and decay endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.main import app
from src.services.memory._entry import MemoryEntry


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestMemoryCRUD:
    @pytest.mark.asyncio
    async def test_manual_writes_commit_without_waiting_for_embedding_provider(
        self, client, auth_headers, monkeypatch
    ):
        from src.services.memory._service import _index_sync

        def forbidden(*args, **kwargs):
            raise AssertionError("Provider publication must run in the durable reconciler")

        monkeypatch.setattr(_index_sync, "index_current_memory", forbidden)
        async with client as c:
            created = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "project.offline.owner", "value": "Alice"},
            )
            assert created.status_code == 200
            updated = await c.put(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "project.offline.owner", "value": "Bob", "expected_revision": 1},
            )
            assert updated.status_code == 200
            assert updated.json()["revision"] == 2
            assert updated.json()["vector_state"] == "pending"
            listed = await c.get("/api/v1/memory", headers=auth_headers)
            row = next(
                item for item in listed.json()["items"] if item["key"] == "project.offline.owner"
            )
            assert row["value"] == "Bob"

    @pytest.mark.asyncio
    async def test_set_memory_success(self, client, auth_headers):
        """Set a new memory"""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "preference", "value": "likes dark mode", "user_id": "default"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "preference"
        assert data["value"] == "likes dark mode"
        assert data["source"] == "manual"

    @pytest.mark.asyncio
    async def test_set_memory_missing_key(self, client, auth_headers):
        """Set memory without key should fail"""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"value": "test value"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_set_memory_missing_value(self, client, auth_headers):
        """Set memory without value should fail"""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "test_key"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_memory_list(self, client, auth_headers):
        """List all memories for a user"""
        # Set some memories first
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "key1", "value": "value1", "user_id": "default"},
            )
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "key2", "value": "value2", "user_id": "default"},
            )

            # List memories
            resp = await c.get("/api/v1/memory", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["memories"]) >= 2
        keys = [m["key"] for m in data["memories"]]
        assert "key1" in keys
        assert "key2" in keys

    @pytest.mark.asyncio
    async def test_memory_list_searches_full_server_dataset_literally(self, client, auth_headers):
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "literal_%_key", "value": "unique searchable value"},
            )
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "unrelated", "value": "other"},
            )

            by_value = await c.get("/api/v1/memory?q=searchable", headers=auth_headers)
            literal_wildcard = await c.get("/api/v1/memory?q=%25", headers=auth_headers)

        assert [item["key"] for item in by_value.json()["items"]] == ["literal_%_key"]
        assert [item["key"] for item in literal_wildcard.json()["items"]] == ["literal_%_key"]

    @pytest.mark.asyncio
    async def test_memory_list_filters_lifecycle_and_fact_type(self, client, auth_headers):
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "filter.pending.decision",
                    "value": "Awaiting approval",
                    "fact_type": "decision",
                    "assertion_status": "pending",
                    "project_id": "filter-project",
                },
            )
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "filter.confirmed.fact", "value": "Confirmed"},
            )
            filtered = await c.get(
                "/api/v1/memory",
                headers=auth_headers,
                params={
                    "fact_type": "decision",
                    "assertion_status": "pending",
                    "project_id": "filter-project",
                },
            )

        assert filtered.status_code == 200
        assert [item["key"] for item in filtered.json()["items"]] == ["filter.pending.decision"]
        assert filtered.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_action_item_fields_round_trip_and_update(self, client, auth_headers):
        async with client as c:
            created = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "todo.atlas.security_review",
                    "value": "Complete the security review",
                    "fact_type": "action_item",
                    "project_id": "atlas",
                    "action_status": "open",
                    "assignee": "Alice",
                    "due_at": "2030-01-02T09:00:00Z",
                },
            )
            assert created.status_code == 200
            revision = created.json()["revision"]
            updated = await c.put(
                "/api/v1/memory",
                headers={**auth_headers, "Idempotency-Key": "complete-atlas-review"},
                json={
                    "key": "todo.atlas.security_review",
                    "expected_revision": revision,
                    "action_status": "done",
                },
            )

        assert updated.status_code == 200
        assert updated.json()["action_status"] == "done"
        assert updated.json()["assignee"] == "Alice"
        assert updated.json()["due_at"].startswith("2030-01-02")

    @pytest.mark.asyncio
    async def test_nullable_memory_fields_can_be_cleared(self, client, auth_headers):
        async with client as c:
            created = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "todo.atlas.clearable",
                    "value": "Clear optional fields",
                    "category": "todo",
                    "fact_type": "action_item",
                    "project_id": "atlas",
                    "action_status": "open",
                    "assignee": "Alice",
                    "due_at": "2030-01-02T09:00:00Z",
                },
            )
            cleared = await c.put(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "todo.atlas.clearable",
                    "expected_revision": created.json()["revision"],
                    "category": None,
                    "project_id": None,
                    "action_status": None,
                    "assignee": None,
                    "due_at": None,
                },
            )

        assert cleared.status_code == 200
        assert cleared.json()["category"] is None
        assert cleared.json()["project_id"] is None
        assert cleared.json()["action_status"] is None
        assert cleared.json()["assignee"] is None
        assert cleared.json()["due_at"] is None

    @pytest.mark.asyncio
    async def test_confirming_disputed_fact_atomically_resolves_conflict(
        self, client, auth_headers, monkeypatch
    ):
        user_id = "default"
        with db.get_write_connection() as conn:
            db.set_memory(
                conn,
                user_id=user_id,
                key="api.owner.alice",
                value="Alice",
                project_id="atlas",
            )
            db.set_memory(
                conn,
                user_id=user_id,
                key="api.owner.bob",
                value="Bob",
                project_id="atlas",
                assertion_status="disputed",
                conflicts_with=["api.owner.alice"],
            )
            candidate = db.get_memory_full(conn, user_id=user_id, key="api.owner.bob")
        monkeypatch.setattr(
            "src.services.memory._service._index_sync.index_current_memory", lambda *_args: True
        )

        async with client as c:
            response = await c.post(
                "/api/v1/memory/resolve-conflict",
                headers=auth_headers,
                json={
                    "winner_key": "api.owner.bob",
                    "expected_revision": candidate["revision"],
                    "conflicting_keys": ["api.owner.alice"],
                },
            )
        assert response.status_code == 200
        assert response.json()["winner"]["assertion_status"] == "confirmed"
        assert response.json()["superseded_keys"] == ["api.owner.alice"]
        with db.get_connection() as conn:
            loser = db.get_memory_full(conn, user_id=user_id, key="api.owner.alice")
        assert loser is not None and loser["assertion_status"] == "superseded"

    @pytest.mark.asyncio
    async def test_delete_memory_success(self, client, auth_headers):
        """Delete an existing memory"""
        async with client as c:
            # Set a memory
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "to_delete", "value": "delete me", "user_id": "default"},
            )

            # Delete it
            resp = await c.delete(
                "/api/v1/memory?key=to_delete",
                headers=auth_headers,
            )
            assert resp.status_code == 200

            # Verify it's gone (same client context)
            resp = await c.get("/api/v1/memory", headers=auth_headers)
            data = resp.json()
            keys = [m["key"] for m in data["memories"]]
            assert "to_delete" not in keys

    @pytest.mark.asyncio
    async def test_delete_memory_missing_key(self, client, auth_headers):
        """Delete memory without key should fail"""
        async with client as c:
            resp = await c.delete("/api/v1/memory", headers=auth_headers)
        assert resp.status_code == 422


class TestMemoryValidation:
    @pytest.mark.asyncio
    async def test_set_memory_key_too_long(self, client, auth_headers):
        """Set memory with key > 200 chars should fail"""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "x" * 201, "value": "test"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_set_memory_value_too_long(self, client, auth_headers):
        """Set memory with value > 10000 chars should fail"""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "test", "value": "x" * 10001},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_memory_upsert(self, client, auth_headers):
        """Setting same key twice should update value"""
        async with client as c:
            # First set
            resp1 = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "upsert_key", "value": "original", "user_id": "default"},
            )
            assert resp1.status_code == 200

            # Update
            resp2 = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "upsert_key", "value": "updated", "user_id": "default"},
            )
            assert resp2.status_code == 200
            assert resp2.json()["value"] == "updated"


class TestMemoryAPIUpdate:
    @pytest.mark.asyncio
    async def test_lifecycle_status_and_version_history(self, client, auth_headers):
        async with client as c:
            created = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "decision.release.date",
                    "value": "October 8",
                    "fact_type": "decision",
                    "assertion_status": "pending",
                    "project_id": "release",
                },
            )
            updated = await c.put(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "decision.release.date",
                    "expected_revision": created.json()["revision"],
                    "assertion_status": "confirmed",
                },
            )
            history = await c.get(
                "/api/v1/memory/versions",
                headers=auth_headers,
                params={"key": "decision.release.date"},
            )

        assert created.status_code == 200
        assert created.json()["assertion_status"] == "pending"
        assert updated.status_code == 200
        assert updated.json()["assertion_status"] == "confirmed"
        assert updated.json()["fact_type"] == "decision"
        assert updated.json()["project_id"] == "release"
        assert history.status_code == 200
        assert [row["revision"] for row in history.json()] == [2, 1]

    @pytest.mark.asyncio
    async def test_update_memory_importance(self, client, auth_headers):
        """PUT /memory updates importance of an existing memory."""
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "upd_imp_key",
                    "value": "some value",
                    "user_id": "default",
                    "importance": 2,
                },
            )
            resp = await c.put(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "upd_imp_key",
                    "user_id": "default",
                    "importance": 5,
                    "expected_revision": 1,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["importance"] == 5

    @pytest.mark.asyncio
    async def test_update_memory_value(self, client, auth_headers):
        """PUT /memory updates value field."""
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "upd_val_key", "value": "old value", "user_id": "default"},
            )
            resp = await c.put(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "upd_val_key",
                    "user_id": "default",
                    "value": "new value",
                    "expected_revision": 1,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["value"] == "new value"

    @pytest.mark.asyncio
    async def test_update_memory_not_found(self, client, auth_headers):
        """PUT /memory returns 404 for unknown key."""
        async with client as c:
            resp = await c.put(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "no_such_key_xyz",
                    "user_id": "default",
                    "importance": 3,
                    "expected_revision": 1,
                },
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_memory_rejects_stale_revision(self, client, auth_headers):
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "cas_key", "value": "v1"},
            )
            first = await c.put(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "cas_key", "value": "v2", "expected_revision": 1},
            )
            stale = await c.put(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "cas_key", "value": "v3", "expected_revision": 1},
            )

        assert first.status_code == 200
        assert stale.status_code == 409
        assert "current revision 2" in stale.json()["detail"]
        assert stale.headers["X-Current-Revision"] == "2"

    @pytest.mark.asyncio
    async def test_memory_validity_window_requires_timezone_and_order(self, client, auth_headers):
        async with client as c:
            missing_timezone = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "time_key", "value": "v", "valid_from": "2026-01-01"},
            )
            reversed_window = await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "time_key",
                    "value": "v",
                    "valid_from": "2026-02-01T00:00:00Z",
                    "valid_to": "2026-01-01T00:00:00Z",
                },
            )

        assert missing_timezone.status_code == 422
        assert reversed_window.status_code == 422


class TestMemoryAPISearch:
    @pytest.mark.asyncio
    async def test_semantic_search_returns_complete_display_record(
        self, client, auth_headers, monkeypatch
    ):
        async def _search(**_kwargs):
            return [
                MemoryEntry(
                    key="search-key",
                    value="search value",
                    importance=4,
                    category="project",
                    source="manual",
                    last_accessed=None,
                    access_count=2,
                    expires_at=None,
                    updated_at="2026-09-04 12:00:00",
                    combined_score=0.91,
                    decay_score=0.8,
                )
            ]

        monkeypatch.setattr(
            "src.api.routers.memory.memory_service.search_semantic",
            _search,
        )
        async with client as c:
            resp = await c.post(
                "/api/v1/memory/search",
                headers=auth_headers,
                json={"query": "project"},
            )

        assert resp.status_code == 200
        item = resp.json()["memories"][0]
        assert item["source"] == "manual"
        assert item["updated_at"] == "2026-09-04 12:00:00"
        assert item["access_count"] == 2
        assert item["combined_score"] == 0.91


class TestMemoryAPIDecay:
    @pytest.mark.asyncio
    async def test_trigger_decay_returns_count(self, client, auth_headers):
        """POST /memory/decay returns decayed_count."""
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "decay_api_k", "value": "v", "user_id": "default"},
            )
            resp = await c.post(
                "/api/v1/memory/decay",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "decayed_count" in data
        assert isinstance(data["decayed_count"], int)


class TestMemoryAPIFeedback:
    @pytest.mark.asyncio
    async def test_feedback_updates_usefulness_and_is_idempotent(self, client, auth_headers):
        headers = {**auth_headers, "Idempotency-Key": "memory-feedback-once"}
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "feedback-key", "value": "feedback value"},
            )

            first = await c.post(
                "/api/v1/memory/feedback",
                headers=headers,
                json={"key": "feedback-key", "useful": True},
            )
            replay = await c.post(
                "/api/v1/memory/feedback",
                headers=headers,
                json={"key": "feedback-key", "useful": True},
            )
            listed = await c.get("/api/v1/memory", headers=auth_headers)

        assert first.status_code == 200
        assert replay.status_code == 200
        assert first.json()["usefulness_score"] == 1.0
        assert first.json()["usefulness_count"] == 1
        assert replay.json() == first.json()
        memory = next(item for item in listed.json()["items"] if item["key"] == "feedback-key")
        assert memory["usefulness_count"] == 1
        assert memory["usefulness_score"] == 1.0
