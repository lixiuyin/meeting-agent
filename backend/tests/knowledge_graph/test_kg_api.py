"""Tests for entity API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.core.database import get_write_connection
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _make_entity(
    user_id: str,
    name: str,
    entity_type: str = "project",
    description: str | None = None,
) -> int:
    """Helper: insert an entity and return its id."""
    with get_write_connection() as conn:
        return db.upsert_entity(
            conn,
            user_id=user_id,
            name=name,
            entity_type=entity_type,
            description=description,
        )


class TestEntityAPI:
    @pytest.mark.asyncio
    async def test_list_entities_empty(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/memory/entities", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_list_entities_with_data(self, client, auth_headers):
        user_id = "default"
        _make_entity(user_id, "Project Beta", "project")
        async with client as c:
            resp = await c.get("/api/v1/memory/entities", headers=auth_headers)
        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()["entities"]]
        assert "project beta" in names

    @pytest.mark.asyncio
    async def test_get_entity_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.get(
                "/api/v1/memory/entities/NoSuchEntity",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_entity_found(self, client, auth_headers):
        user_id = "default"
        _make_entity(user_id, "FoundEntity", "concept", "A findable entity")
        async with client as c:
            resp = await c.get(
                "/api/v1/memory/entities/FoundEntity",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["entity"]["name"] == "foundentity"

    @pytest.mark.asyncio
    async def test_delete_entity_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.delete("/api/v1/memory/entities/Missing", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_entity_success(self, client, auth_headers):
        user_id = "default"
        _make_entity(user_id, "ToBeDeleted", "concept")
        async with client as c:
            resp = await c.delete(
                "/api/v1/memory/entities/ToBeDeleted",
                headers=auth_headers,
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_merge_entities_target_not_found(self, client, auth_headers):
        async with client as c:
            resp = await c.post(
                "/api/v1/memory/entities/merge",
                json={"user_id": "default", "source_names": ["A"], "target_name": "NoTarget"},
                headers=auth_headers,
            )
        assert resp.status_code == 400
