"""Regression tests for bounded multi-hop memory retrieval."""

from unittest.mock import MagicMock, patch

import pytest

from src.core.config import settings
from src.services.memory import MemoryService
from src.services.memory._entry import MemoryEntry
from src.services.memory._service._search import (
    _build_multi_hop_query,
    _select_with_bridge_reserve,
)


def _record(value: str) -> dict:
    return {
        "value": value,
        "importance": 3,
        "updated_at": "2026-01-01",
        "is_legacy_scope": 0,
    }


def test_multi_hop_seed_selection_prefers_lexical_bridge_facts() -> None:
    expanded = _build_multi_hop_query(
        "Which dependency blocking Bob's work was resolved, and when?",
        [
            {
                "content": "distractor: unrelated inventory archive batch ZX-030",
                "score": 0.01,
            },
            {
                "content": "owner: Bob owns the database migration.",
                "score": 0.20,
            },
            {
                "content": "dependency: database migration depends on storage approval.",
                "score": 0.30,
            },
        ],
        seed_count=2,
    )

    assert expanded is not None
    assert "Bob owns the database migration" in expanded
    assert "depends on storage approval" in expanded
    assert "unrelated inventory" not in expanded


def test_multi_hop_final_selection_reserves_bridge_candidate() -> None:
    entries = [
        MemoryEntry(
            key=f"distractor_{index}",
            value="unrelated",
            importance=5,
            category=None,
            source="test",
            last_accessed=None,
            access_count=0,
            expires_at=None,
            updated_at="2026-01-01",
            combined_score=0.9 - index / 100,
        )
        for index in range(4)
    ]
    bridge = MemoryEntry(
        key="storage_budget_resolution",
        value="Approved January 20",
        importance=5,
        category=None,
        source="test",
        last_accessed=None,
        access_count=0,
        expires_at=None,
        updated_at="2026-01-01",
        combined_score=0.4,
    )
    entries.append(bridge)

    selected = _select_with_bridge_reserve(
        entries,
        bridge_scores={bridge.key: 0.8},
        limit=3,
        reserve_count=1,
    )

    assert [entry.key for entry in selected] == [bridge.key, "distractor_0", "distractor_1"]


@pytest.mark.asyncio
async def test_second_hop_recovers_linked_resolution_fact(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MemoryService()
    vector_store = MagicMock()
    vector_store.similarity_search.side_effect = [
        [
            {
                "key": "owner",
                "content": "owner: Bob owns the database migration.",
                "score": 0.10,
                "meeting_ids": None,
                "file_ids": None,
            },
            {
                "key": "dependency",
                "content": "dependency: The migration depends on storage budget approval.",
                "score": 0.20,
                "meeting_ids": None,
                "file_ids": None,
            },
        ],
        [
            {
                "key": "resolution",
                "content": "resolution: Finance approved the storage budget on January 20.",
                "score": 0.05,
                "meeting_ids": None,
                "file_ids": None,
            }
        ],
    ]
    batch = {
        "owner": _record("Bob owns the database migration."),
        "dependency": _record("The migration depends on storage budget approval."),
        "resolution": _record("Finance approved the storage budget on January 20."),
    }
    monkeypatch.setattr(settings, "MEMORY_MULTI_HOP_ENABLED", True)
    monkeypatch.setattr(settings, "MEMORY_MULTI_HOP_SEED_COUNT", 2)

    with (
        patch(
            "src.services.memory._service._search.get_memory_vectorstore",
            return_value=vector_store,
        ),
        patch(
            "src.services.memory._service._search.db.get_memories_batch",
            return_value=batch,
        ),
        patch.object(service, "search_important", return_value=[]),
    ):
        entries = await service.search_semantic(
            "user",
            query="Which dependency blocking Bob's work was resolved, and when?",
            limit=3,
        )

    assert {entry.key for entry in entries} == {"owner", "dependency", "resolution"}
    assert vector_store.similarity_search.call_count == 2
    expanded_query = vector_store.similarity_search.call_args_list[1].args[0]
    assert "storage budget approval" in expanded_query
    assert vector_store.similarity_search.call_args_list[1].args[2] == 12


@pytest.mark.asyncio
async def test_multi_hop_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MemoryService()
    vector_store = MagicMock()
    vector_store.similarity_search.return_value = [
        {"key": "a", "content": "a: first", "score": 0.1},
        {"key": "b", "content": "b: second", "score": 0.2},
    ]
    monkeypatch.setattr(settings, "MEMORY_MULTI_HOP_ENABLED", False)

    with (
        patch(
            "src.services.memory._service._search.get_memory_vectorstore",
            return_value=vector_store,
        ),
        patch(
            "src.services.memory._service._search.db.get_memories_batch",
            return_value={"a": _record("first"), "b": _record("second")},
        ),
        patch.object(service, "search_important", return_value=[]),
    ):
        await service.search_semantic("user", query="query", limit=2)

    assert vector_store.similarity_search.call_count == 1
