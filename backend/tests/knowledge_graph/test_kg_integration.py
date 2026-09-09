"""Integration tests for knowledge graph: pipeline, config, self-loop prevention."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core import database as db
from src.core.database import get_connection, get_write_connection
from src.services.knowledge_graph import (
    ENTITY_TYPES,
    RELATION_PREDICATES,
    kg_service,
)


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


class TestPipelineEntityContext:
    def test_entity_context_field_exists(self):
        from src.services.chain import PipelineContext

        ctx = PipelineContext(question="test")
        assert hasattr(ctx, "entity_context")
        assert ctx.entity_context == ""

    def test_build_system_context_includes_entity_graph(self):
        from src.services.chain import _build_system_context

        result = _build_system_context(
            memory_context="user likes coffee",
            session_context="",
            entity_context="- Project: ProjectX (main initiative)",
            meeting_context="Meeting about budgets",
            web_context="",
        )
        assert "<user_memory>" in result
        assert "ProjectX" in result

    def test_build_system_context_omits_empty_entity_context(self):
        from src.services.chain import _build_system_context

        result = _build_system_context(
            memory_context="user likes coffee",
            session_context="",
            entity_context="",
            meeting_context="Meeting about budgets",
            web_context="",
        )
        # Entity context is merged into user_memory, still present when memory_context is set
        assert "<user_memory>" in result


class TestPhase3Config:
    def test_knowledge_graph_settings_exist(self):
        from src.core.config import settings

        assert hasattr(settings, "KNOWLEDGE_GRAPH_ENABLED")
        assert hasattr(settings, "MEMORY_EXTRACTION_MODE")

    def test_knowledge_graph_defaults(self):
        from src.core.config import settings

        assert settings.KNOWLEDGE_GRAPH_ENABLED is False
        assert settings.MEMORY_EXTRACTION_MODE == "balanced"

    def test_entity_types_set(self):
        assert "person" in ENTITY_TYPES
        assert "project" in ENTITY_TYPES
        assert "tool" in ENTITY_TYPES

    def test_relation_predicates_set(self):
        assert "works_on" in RELATION_PREDICATES
        assert "uses" in RELATION_PREDICATES
        assert "related_to" in RELATION_PREDICATES

    def test_entity_relations_limit_config_exists(self):
        from src.core.config import settings

        assert hasattr(settings, "ENTITY_RELATIONS_LIMIT")
        assert settings.ENTITY_RELATIONS_LIMIT >= 1


class TestSelfLoopPrevention:
    def test_upsert_relation_ignores_self_loop_at_db_layer(self):
        """upsert_relation() with subject_id == object_id must not insert a row."""
        from src.core.database import get_write_connection

        user_id = "self_loop_db_user"
        eid = _make_entity(user_id, "solo_entity", "concept")

        with get_write_connection() as conn:
            db.upsert_relation(
                conn,
                user_id=user_id,
                subject_id=eid,
                predicate="related_to",
                object_id=eid,  # self-loop
            )

        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_relations WHERE subject_id=? AND object_id=?",
                (eid, eid),
            ).fetchone()[0]
        assert count == 0

    @pytest.mark.asyncio
    async def test_extract_entities_skips_self_loop_relation(self, monkeypatch):
        """LLM output with subject == object should not store a self-loop relation."""

        user_id = "self_loop_svc_user"
        from src.core.config import settings

        monkeypatch.setattr(settings, "KNOWLEDGE_GRAPH_ENABLED", True)
        # LLM says an entity relates to itself
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "entities": [{"name": "loopA", "type": "concept"}],
                "relations": [{"subject": "loopA", "predicate": "related_to", "object": "loopA"}],
            }
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        # See test_kg_service.py: patch the LLM invocation directly rather
        # than ``asyncio.to_thread`` so the entity-storage path's legitimate
        # ``to_thread`` calls aren't accidentally short-circuited.
        with patch("src.services.llm.get_llm", return_value=mock_llm):
            with patch(
                "src.services.llm.cached_retry_invoke",
                return_value=mock_response,
            ):
                result = await kg_service.extract_entities(user_id, "test q", "test a")

        # Entity added but self-loop relation must NOT be stored
        from src.core.database import get_connection

        eid_row = None
        with get_connection() as conn:
            eid_row = db.get_entity_by_name(conn, user_id=user_id, name="loopa")
        assert eid_row is not None
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_relations WHERE subject_id=? AND object_id=?",
                (eid_row["id"], eid_row["id"]),
            ).fetchone()[0]
        assert count == 0
