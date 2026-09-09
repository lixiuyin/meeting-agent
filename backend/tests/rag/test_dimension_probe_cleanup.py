from unittest.mock import Mock

import pytest

from src.services.rag._vectorstore import _ensure_collection_dimension


@pytest.mark.parametrize("branch", ["missing", "empty", "matching", "mismatch"])
def test_dimension_probe_releases_its_client_on_every_exit(monkeypatch, branch):
    client = Mock()
    if branch == "missing":
        client.get_collection.side_effect = ValueError("missing")
    else:
        collection = client.get_collection.return_value
        collection.count.return_value = 0 if branch == "empty" else 1
        collection.metadata = {"embedding_dimension": 8 if branch == "mismatch" else 4}
    monkeypatch.setattr("chromadb.PersistentClient", Mock(return_value=client))
    if branch == "mismatch":
        with pytest.raises(RuntimeError, match="dimension mismatch"):
            _ensure_collection_dimension("unused", "fixture", None, 4)
    else:
        _ensure_collection_dimension("unused", "fixture", None, 4)
    client.close.assert_called_once()
