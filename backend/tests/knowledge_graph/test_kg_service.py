"""Tests for KnowledgeGraphService."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core import database as db
from src.core.database import get_write_connection
from src.services.knowledge_graph import kg_service


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


class TestKnowledgeGraphService:
    def test_get_entities_empty(self):
        entities = kg_service.get_entities("empty_kg_user")
        assert entities == []

    def test_get_entity_with_relations_not_found(self):
        result = kg_service.get_entity_with_relations("no_such_user", "NoSuchEntity")
        assert result is None

    def test_get_entity_with_relations_found(self):
        user_id = "kg_rel_user"
        aid = _make_entity(user_id, "TeamLead", "person")
        bid = _make_entity(user_id, "ProjectZ", "project")
        with get_write_connection() as conn:
            db.upsert_relation(
                conn, user_id=user_id, subject_id=aid, predicate="leads", object_id=bid
            )

        result = kg_service.get_entity_with_relations(user_id, "TeamLead")
        assert result is not None
        assert result["entity"]["name"] == "teamlead"
        assert len(result["relations"]) == 1
        assert result["relations"][0]["predicate"] == "leads"

    def test_delete_entity_by_name(self):
        user_id = "delete_by_name_user"
        _make_entity(user_id, "EphemeralEntity", "concept")
        assert kg_service.delete_entity(user_id, "EphemeralEntity") is True
        assert kg_service.get_entity_with_relations(user_id, "EphemeralEntity") is None

    def test_delete_nonexistent_entity_returns_false(self):
        assert kg_service.delete_entity("any_user", "TotallyMissing") is False

    def test_merge_entities(self):
        user_id = "merge_kg_user"
        tgt = _make_entity(user_id, "MasterEntity", "concept")
        src1 = _make_entity(user_id, "Duplicate1", "concept")
        other = _make_entity(user_id, "RelatedThing", "concept")
        with get_write_connection() as conn:
            db.upsert_relation(
                conn, user_id=user_id, subject_id=src1, predicate="related_to", object_id=other
            )

        ok = kg_service.merge_entities(
            user_id, source_names=["Duplicate1"], target_name="MasterEntity"
        )
        assert ok is True
        # Duplicate1 should be gone
        assert kg_service.get_entity_with_relations(user_id, "Duplicate1") is None
        # MasterEntity should have the reassigned relation
        result = kg_service.get_entity_with_relations(user_id, "MasterEntity")
        assert result is not None
        assert any(r["other_name"] == "relatedthing" for r in result["relations"])

    def test_merge_returns_false_for_missing_target(self):
        ok = kg_service.merge_entities("x_user", source_names=["A"], target_name="NonExistent")
        assert ok is False

    @pytest.mark.asyncio
    async def test_extract_entities_disabled_by_setting(self):
        with patch("src.services.knowledge_graph._service.settings") as mock_s:
            mock_s.KNOWLEDGE_GRAPH_ENABLED = False
            result = await kg_service.extract_entities("u", "q", "a")
        assert result == {"entities_added": 0, "relations_added": 0}

    @pytest.mark.asyncio
    async def test_extract_entities_precise_mode_skips(self):
        with patch("src.services.knowledge_graph._service.settings") as mock_s:
            mock_s.KNOWLEDGE_GRAPH_ENABLED = True
            mock_s.MEMORY_EXTRACTION_MODE = "precise"
            result = await kg_service.extract_entities("u", "q", "a")
        assert result == {"entities_added": 0, "relations_added": 0}

    @pytest.mark.asyncio
    async def test_extract_entities_calls_llm_and_stores(self):
        user_id = "extract_kg_user"
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "entities": [
                    {"name": "OpenAI", "type": "organization", "description": "AI company"},
                    {"name": "GPT-4", "type": "tool"},
                ],
                "relations": [{"subject": "OpenAI", "predicate": "uses", "object": "GPT-4"}],
            }
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        # Patch the LLM invocation directly rather than ``asyncio.to_thread``;
        # _store_entities now legitimately uses ``to_thread`` to offload sync
        # vector-store work, so a global to_thread patch leaks the mocked LLM
        # response into the entity-storage path.
        with patch("src.services.llm.get_llm", return_value=mock_llm):
            with patch(
                "src.services.llm.cached_retry_invoke",
                return_value=mock_response,
            ):
                result = await kg_service.extract_entities(
                    user_id, "Tell me about GPT-4", "OpenAI made GPT-4."
                )

        assert result["entities_added"] >= 2

    @pytest.mark.asyncio
    async def test_get_entity_context_returns_empty_when_disabled(self):
        with patch("src.services.knowledge_graph._service.settings") as mock_s:
            mock_s.KNOWLEDGE_GRAPH_ENABLED = False
            ctx = await kg_service.get_entity_context("u", "any query")
        assert ctx == ""

    @pytest.mark.asyncio
    async def test_get_entity_context_filters_by_meeting(self):
        user_id = "scope_entity_user"
        with get_write_connection() as conn:
            eid_scoped = db.upsert_entity(
                conn, user_id=user_id, name="alpha", entity_type="project"
            )
            eid_other = db.upsert_entity(conn, user_id=user_id, name="beta", entity_type="project")
            eid_global = db.upsert_entity(
                conn, user_id=user_id, name="gamma", entity_type="project"
            )

        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = [
            {"entity_id": eid_scoped, "meeting_ids": [101], "score": 0.1},
            {"entity_id": eid_other, "meeting_ids": [202], "score": 0.2},
            {"entity_id": eid_global, "meeting_ids": None, "score": 0.3},
        ]
        with patch(
            "src.services.knowledge_graph._service.get_entity_vectorstore", return_value=mock_vs
        ):
            context = await kg_service.get_entity_context(
                user_id,
                "query",
                top_k=5,
                meeting_ids=[101],
            )

        assert "**alpha**" in context
        assert "**beta**" not in context
        assert "**gamma**" in context
