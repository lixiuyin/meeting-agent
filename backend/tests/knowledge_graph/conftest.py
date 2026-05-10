"""Shared fixtures for knowledge graph tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_entity_vectorstore(monkeypatch):
    """Mock entity vectorstore that records upsert calls."""
    vs = MagicMock()
    vs.upsert.return_value = "entity_vec_123"
    vs.similarity_search.return_value = []
    monkeypatch.setattr(
        "src.services.knowledge_graph._storage.get_entity_vectorstore",
        lambda: vs,
    )
    return vs
