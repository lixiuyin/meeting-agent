"""Integration tests for the skills API router."""

import pytest
from httpx import ASGITransport, AsyncClient

from skills.loader import SkillLoader as RealSkillLoader
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestSkillsListEndpoint:
    @pytest.mark.asyncio
    async def test_list_skills_returns_tech_proposal(self, client, auth_headers):
        async with client as c:
            resp = await c.get("/api/v1/skills", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data
        assert data["total"] >= 1
        names = {s["name"] for s in data["skills"]}
        assert "tech_proposal_generator" in names


class TestSkillsCreateEndpoint:
    @pytest.mark.asyncio
    async def test_create_skill_success(self, client, auth_headers, monkeypatch, tmp_path):
        import src.api.routers.skills as skills_mod

        temp_loader = RealSkillLoader(tmp_path)
        monkeypatch.setattr(skills_mod, "_loader", temp_loader)

        payload = {
            "name": "custom_notes_generator",
            "display_name": "Custom Notes Generator",
            "description": "Generate custom notes based on selected meetings.",
            "required_keywords": ["custom notes"],
            "optional_keywords": ["notes", "recap"],
            "examples": ["Generate custom notes"],
        }
        async with client as c:
            resp = await c.post("/api/v1/skills", json=payload, headers=auth_headers)

        assert resp.status_code == 201
        data = resp.json()
        assert data["message"] == "Skill created"
        assert data["skill"]["name"] == "custom_notes_generator"
        assert (tmp_path / "custom_notes" / "skill.md").exists()

    @pytest.mark.asyncio
    async def test_create_skill_conflict(self, client, auth_headers, monkeypatch, tmp_path):
        import src.api.routers.skills as skills_mod

        temp_loader = RealSkillLoader(tmp_path)
        monkeypatch.setattr(skills_mod, "_loader", temp_loader)

        skill_dir = tmp_path / "dup_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            """---
name: dup_skill_generator
display_name: "Dup Skill"
description: "dup skill description"
intent_matching:
  method: hybrid
---

# Dup Skill
""",
            encoding="utf-8",
        )

        payload = {
            "name": "dup_skill_generator",
            "display_name": "Dup Skill",
            "description": "dup skill description",
        }
        async with client as c:
            resp = await c.post("/api/v1/skills", json=payload, headers=auth_headers)

        assert resp.status_code == 409


class TestSkillsMatchEndpoint:
    @pytest.mark.asyncio
    async def test_match_intent_matched(self, client, auth_headers, monkeypatch):
        from skills.models import (
            IntentMatchingConfig,
            SkillMatchResult,
            SkillSummary,
        )

        fake_summary = SkillSummary(
            name="tech_proposal_generator",
            display_name="Tech Proposal",
            description="test",
            intent_matching=IntentMatchingConfig(
                method="keyword", keywords={"required": ["technical proposal"]}, threshold=0.5
            ),
        )
        fake_result = SkillMatchResult(skill=fake_summary, score=0.85, matched=True)

        async def _mock_match(self, query, skills):
            return fake_result

        monkeypatch.setattr("src.api.routers.skills.IntentMatchingService.match", _mock_match)

        async with client as c:
            resp = await c.post(
                "/api/v1/skills/match",
                params={"query": "Please generate a MOST technical proposal"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is True
        assert data["skill"]["name"] == "tech_proposal_generator"
        assert data["score"] == 0.85

    @pytest.mark.asyncio
    async def test_match_intent_no_match(self, client, auth_headers, monkeypatch):
        async def _mock_match(self, query, skills):
            return None

        monkeypatch.setattr("src.api.routers.skills.IntentMatchingService.match", _mock_match)

        async with client as c:
            resp = await c.post(
                "/api/v1/skills/match",
                params={"query": "What should I eat today"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is False
        assert data["reason"] == "No skill matched"


class TestSkillsInvokeEndpoint:
    @pytest.mark.asyncio
    async def test_invoke_existing_skill(self, client, auth_headers, monkeypatch):
        calls = []

        async def _mock_run_pipeline(ctx, skill_definition=None):
            calls.append(skill_definition)
            ctx.answer = "# Mocked skill output"
            ctx.docs = []

        monkeypatch.setattr(
            "src.api.routers.skills._run_pipeline",
            _mock_run_pipeline,
        )

        async with client as c:
            resp = await c.post(
                "/api/v1/skills/invoke",
                json={
                    "skill_name": "tech_proposal_generator",
                    "query": "Generate a technical proposal",
                    "user_id": "test_user",
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skill_name"] == "tech_proposal_generator"
        assert data["content"] == "# Mocked skill output"
        assert data["format"] == "markdown"
        assert len(calls) == 1
        assert calls[0] is not None

    @pytest.mark.asyncio
    async def test_invoke_missing_skill_returns_404(self, client, auth_headers):
        async with client as c:
            resp = await c.post(
                "/api/v1/skills/invoke",
                json={
                    "skill_name": "nonexistent_skill_xyz",
                    "query": " anything",
                },
                headers=auth_headers,
            )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
