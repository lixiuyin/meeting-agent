"""Tests for memory API CRUD, validation, update, and decay endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestMemoryCRUD:
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
                json={"key": "upd_imp_key", "user_id": "default", "importance": 5},
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
                json={"key": "upd_val_key", "user_id": "default", "value": "new value"},
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
                json={"key": "no_such_key_xyz", "user_id": "default", "importance": 3},
            )
        assert resp.status_code == 404


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
