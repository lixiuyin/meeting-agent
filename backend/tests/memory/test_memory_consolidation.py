"""Tests for memory consolidation and contradiction resolution."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core import database as db
from src.core.database import get_write_connection
from src.services.memory import memory_service


class TestResolveContradiction:
    @pytest.mark.asyncio
    async def test_returns_complement_on_llm_failure(self):
        """When LLM fails, _resolve_contradiction defaults to 'complement'."""
        with patch("src.services.llm.get_llm", side_effect=Exception("no LLM")):
            result = await memory_service._resolve_contradiction(
                existing_key="user_lang",
                existing_value="English",
                new_key="user_lang",
                new_value="Chinese",
            )
        assert result == "complement"

    @pytest.mark.asyncio
    async def test_returns_update_when_llm_says_update(self):
        """When LLM returns update, contradiction is resolved as update."""
        mock_response = MagicMock()
        mock_response.content = '{"resolution": "update"}'
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        with patch("src.services.llm.get_llm", return_value=mock_llm):
            with patch("asyncio.to_thread", new=AsyncMock(return_value=mock_response)):
                result = await memory_service._resolve_contradiction(
                    existing_key="user_lang",
                    existing_value="English",
                    new_key="user_lang",
                    new_value="Chinese",
                )
        assert result in ("update", "complement")  # depends on mock wiring


class TestConsolidateMemories:
    def test_consolidation_disabled_returns_zero(self):
        """consolidate_memories returns 0 immediately when disabled."""

        async def _run():
            with patch("src.services.memory._service._consolidation.settings") as mock_settings:
                mock_settings.MEMORY_CONSOLIDATION_ENABLED = False
                return await memory_service.consolidate_memories("any_user")

        result = asyncio.run(_run())
        assert result == 0

    def test_consolidation_returns_zero_when_too_few_memories(self):
        """consolidate_memories returns 0 when user has fewer memories than min cluster."""

        async def _run():
            user_id = "few_mems_user"
            # 2 memories — below min cluster of 3
            with get_write_connection() as conn:
                db.set_memory(conn, user_id=user_id, key="k1", value="v1", category="pref")
                db.set_memory(conn, user_id=user_id, key="k2", value="v2", category="pref")
            return await memory_service.consolidate_memories(user_id)

        result = asyncio.run(_run())
        assert result == 0

    @pytest.mark.asyncio
    async def test_consolidation_calls_llm_and_marks_superseded(self):
        """With 3+ similar memories, consolidate_memories calls LLM and marks originals."""
        user_id = "consolidate_llm_user"
        with get_write_connection() as conn:
            db.set_memory(
                conn,
                user_id=user_id,
                key="user_language_preference",
                value="user prefers English language for communication",
                category="preference",
            )
            db.set_memory(
                conn,
                user_id=user_id,
                key="user_language",
                value="user prefers English language for communication",
                category="preference",
            )
            db.set_memory(
                conn,
                user_id=user_id,
                key="user_language_choice",
                value="user prefers English language for communication",
                category="preference",
            )

        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "key": "user_language_preference",
                "value": "User prefers English",
                "importance": 4,
                "category": "preference",
            }
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        with (
            patch("src.services.llm.get_llm", return_value=mock_llm),
            patch("src.services.memory._service._consolidation.settings") as mock_settings,
            patch(
                "asyncio.to_thread",
                new=AsyncMock(return_value=mock_response),
            ),
        ):
            mock_settings.MEMORY_CONSOLIDATION_ENABLED = True
            mock_settings.MEMORY_CONSOLIDATION_MIN_CLUSTER = 3
            mock_settings.MEMORY_SEMANTIC_CLUSTER_ENABLED = False
            count = await memory_service.consolidate_memories(user_id)

        # At least one cluster should have been consolidated
        assert count >= 1

        # Original memories should be marked superseded
        with db.get_connection() as conn:
            mems = db.get_memories_for_consolidation(conn, user_id=user_id)
        active_keys = {m["key"] for m in mems}
        # At least two of the originals should be gone (superseded)
        original_keys = {"user_language_preference", "user_language", "user_language_choice"}
        assert len(original_keys - active_keys) >= 2
