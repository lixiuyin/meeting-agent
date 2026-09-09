"""Tests for memory API entity endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.core.database import get_connection, get_write_connection
from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestMemoryAPIEntityEndpoints:
    @pytest.mark.asyncio
    async def test_list_entities_empty(self, client, auth_headers):
        """GET /memory/entities returns empty list for new user."""
        async with client as c:
            resp = await c.get(
                "/api/v1/memory/entities",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["entities"] == []

    @pytest.mark.asyncio
    async def test_list_entities_with_type_filter(self, client, auth_headers):
        """GET /memory/entities?entity_type=... returns only matching entities."""
        user_id = "default"
        with get_write_connection() as conn:
            db.upsert_entity(conn, user_id=user_id, name="alice", entity_type="person")
            db.upsert_entity(conn, user_id=user_id, name="project-x", entity_type="project")

        async with client as c:
            resp = await c.get(
                "/api/v1/memory/entities?entity_type=person",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["entities"][0]["entity_type"] == "person"

    @pytest.mark.asyncio
    async def test_list_entities_supports_cursor_pagination(self, client, auth_headers):
        user_id = "default"
        entity_type = "pagination-test"
        with get_write_connection() as conn:
            for index in range(3):
                db.upsert_entity(
                    conn,
                    user_id=user_id,
                    name=f"paged-entity-{index}",
                    entity_type=entity_type,
                )

        async with client as c:
            first = await c.get(
                "/api/v1/memory/entities",
                params={"entity_type": entity_type, "limit": 2},
                headers=auth_headers,
            )
            cursor = first.json()["next_cursor"]
            second = await c.get(
                "/api/v1/memory/entities",
                params={"entity_type": entity_type, "limit": 2, "cursor": cursor},
                headers=auth_headers,
            )

        assert first.json()["total"] == 3
        assert len(first.json()["entities"]) == 2
        assert cursor
        assert len(second.json()["entities"]) == 1

    @pytest.mark.asyncio
    async def test_batch_delete_entities(self, client, auth_headers):
        with get_write_connection() as conn:
            db.upsert_entity(conn, user_id="default", name="batch-ent-1", entity_type="concept")
            db.upsert_entity(conn, user_id="default", name="batch-ent-2", entity_type="concept")

        async with client as c:
            resp = await c.post(
                "/api/v1/memory/entities/batch-delete",
                headers=auth_headers,
                json={"names": ["batch-ent-1", "batch-ent-2", "missing-ent"]},
            )

        assert resp.status_code == 200
        assert resp.json() == {"deleted": 2, "missing": ["missing-ent"]}

    @pytest.mark.asyncio
    async def test_get_entity_not_found(self, client, auth_headers):
        """GET /memory/entities/{name} returns 404 for unknown entity."""
        async with client as c:
            resp = await c.get(
                "/api/v1/memory/entities/totally_unknown_entity",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_entity_success(self, client, auth_headers):
        """DELETE /memory/entities/{name} removes the entity."""
        user_id = "default"
        with get_write_connection() as conn:
            db.upsert_entity(conn, user_id=user_id, name="to_delete_ent", entity_type="concept")

        async with client as c:
            resp = await c.delete(
                "/api/v1/memory/entities/to_delete_ent",
                headers=auth_headers,
            )
        assert resp.status_code == 200

        with get_connection() as conn:
            assert db.get_entity_by_name(conn, user_id=user_id, name="to_delete_ent") is None

    @pytest.mark.asyncio
    async def test_delete_entity_not_found(self, client, auth_headers):
        """DELETE /memory/entities/{name} returns 404 for unknown entity."""
        async with client as c:
            resp = await c.delete(
                "/api/v1/memory/entities/no_such_entity_api",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_entity_name_normalization(self, client, auth_headers):
        """Entity names are normalized to lowercase on write and lookup."""
        user_id = "default"
        # Insert with mixed case
        with get_write_connection() as conn:
            db.upsert_entity(conn, user_id=user_id, name="GPT-4", entity_type="tool")

        # Lookup with different case should still find it
        async with client as c:
            resp = await c.get(
                "/api/v1/memory/entities/gpt-4",
                headers=auth_headers,
            )
        assert resp.status_code == 200

        # Also verify the stored name is normalized
        with get_connection() as conn:
            ent = db.get_entity_by_name(conn, user_id=user_id, name="gpt-4")
        assert ent is not None
        assert ent["name"] == "gpt-4"
