"""Security regressions for user-id isolation."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.security import _derive_user_id_from_api_key
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_skills_invoke_ignores_client_supplied_user_id(client, auth_headers):
    captured: dict[str, str] = {}

    async def _fake_run_pipeline(ctx, _skill_payload):
        captured["user_id"] = ctx.user_id
        ctx.answer = "ok"
        ctx.docs = []

    class _Skill:
        name = "meeting_minutes"
        output = type("Output", (), {"format": "markdown"})()

        def model_dump(self):
            return {"name": self.name}

    with (
        patch("src.api.routers.skills._run_pipeline", _fake_run_pipeline),
        patch("src.api.routers.skills._extract_sources", lambda _docs: []),
        patch("src.api.routers.skills._loader.get_full", return_value=_Skill()),
    ):
        async with client as c:
            resp = await c.post(
                "/api/v1/skills/invoke",
                headers=auth_headers,
                json={
                    "skill_name": "meeting_minutes",
                    "query": "summarize",
                    "user_id": "attacker-user",
                },
            )

    assert resp.status_code == 200
    assert captured["user_id"] == _derive_user_id_from_api_key("test-api-key")
