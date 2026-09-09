"""Tests for memory API batch import and export endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.security import _derive_user_id_from_api_key
from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestMemoryBatchImport:
    @pytest.mark.asyncio
    async def test_batch_import_success(self, client, auth_headers):
        """POST /memory/batch stores all provided memories."""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory/batch",
                headers=auth_headers,
                json={
                    "user_id": "default",
                    "memories": [
                        {"key": "lang", "value": "Python", "importance": 4},
                        {"key": "tool", "value": "VS Code", "importance": 3},
                    ],
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert data["failed"] == 0

    @pytest.mark.asyncio
    async def test_batch_import_empty_list_fails(self, client, auth_headers):
        """POST /memory/batch with empty memories list should fail validation."""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory/batch",
                headers=auth_headers,
                json={"user_id": "default", "memories": []},
            )
        # Pydantic min_length=1 on the list should reject empty
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_batch_import_over_limit_fails(self, client, auth_headers):
        """POST /memory/batch with > 100 items should fail validation."""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory/batch",
                headers=auth_headers,
                json={
                    "user_id": "default",
                    "memories": [{"key": f"k{i}", "value": f"v{i}"} for i in range(101)],
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_batch_import_missing_memories_fails(self, client, auth_headers):
        """POST /memory/batch without memories field should fail validation."""
        async with client as c:
            resp = await c.post(
                "/api/v1/memory/batch",
                headers=auth_headers,
                json={"user_id": "default"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_batch_delete_removes_existing_and_reports_missing(self, client, auth_headers):
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "batch_delete_me", "value": "value"},
            )
            resp = await c.post(
                "/api/v1/memory/batch-delete",
                headers=auth_headers,
                json={"keys": ["batch_delete_me", "not_present"]},
            )

        assert resp.status_code == 200
        assert resp.json() == {"deleted": 1, "missing": ["not_present"]}


class TestMemoryExport:
    @pytest.mark.asyncio
    async def test_export_returns_all_memories(self, client, auth_headers):
        """GET /memory/export returns the correct shape and count."""
        async with client as c:
            # Seed two memories
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "exp_k1", "value": "v1", "user_id": "default"},
            )
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={"key": "exp_k2", "value": "v2", "user_id": "default"},
            )

            resp = await c.get(
                "/api/v1/memory/export",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == _derive_user_id_from_api_key("test-api-key")
        assert data["total"] >= 2
        keys = [m["key"] for m in data["memories"]]
        assert "exp_k1" in keys
        assert "exp_k2" in keys

    @pytest.mark.asyncio
    async def test_export_is_cursor_paginated_with_full_total(self, client, auth_headers):
        async with client as c:
            for key in ("page_export_1", "page_export_2"):
                await c.post(
                    "/api/v1/memory",
                    headers=auth_headers,
                    json={"key": key, "value": "value"},
                )
            first = await c.get("/api/v1/memory/export?limit=1", headers=auth_headers)
            cursor = first.json()["next_cursor"]
            second = await c.get(
                "/api/v1/memory/export",
                params={"limit": 1, "cursor": cursor},
                headers=auth_headers,
            )

        assert first.status_code == 200
        assert first.json()["total"] >= 2
        assert cursor
        assert second.status_code == 200
        assert second.json()["memories"][0]["key"] != first.json()["memories"][0]["key"]

    @pytest.mark.asyncio
    async def test_export_items_are_reimportable(self, client, auth_headers):
        """Exported memory items can be round-tripped through /batch."""
        async with client as c:
            await c.post(
                "/api/v1/memory",
                headers=auth_headers,
                json={
                    "key": "rt_key",
                    "value": "rt_value",
                    "user_id": "default",
                    "fact_type": "decision",
                    "assertion_status": "pending",
                    "project_id": "roundtrip-project",
                },
            )
            export_resp = await c.get(
                "/api/v1/memory/export",
                headers=auth_headers,
            )
            assert export_resp.status_code == 200
            exported = export_resp.json()["memories"]
            roundtrip_item = next(item for item in exported if item["key"] == "rt_key")
            assert roundtrip_item["fact_type"] == "decision"
            assert roundtrip_item["assertion_status"] == "pending"
            assert roundtrip_item["project_id"] == "roundtrip-project"

            # Round-trip via batch
            import_resp = await c.post(
                "/api/v1/memory/batch",
                headers=auth_headers,
                json={"user_id": "default", "memories": exported},
            )
        assert import_resp.status_code == 200
        assert import_resp.json()["imported"] == len(exported)
