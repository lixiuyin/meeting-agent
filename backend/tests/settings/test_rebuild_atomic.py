"""Safety checks for shadow vector rebuilds."""

from unittest.mock import MagicMock

import pytest

from src.api.routers.settings._rebuild import _try_copy_collection_chunks


def _client_with(source: MagicMock, target: MagicMock) -> MagicMock:
    client = MagicMock()
    client.get_collection.side_effect = lambda name: source if name == "meetings" else target
    return client


def test_fast_copy_rejects_stale_fingerprint_before_writing() -> None:
    source = MagicMock(metadata={"embedding_dimension": 4})
    target = MagicMock(metadata={"embedding_dimension": 4})
    source.count.return_value = 1
    source.get.return_value = {
        "ids": ["chunk-1"],
        "metadatas": [{"index_config_fingerprint": "old"}],
    }

    copied = _try_copy_collection_chunks(
        _client_with(source, target), "meetings", "shadow", "active"
    )

    assert copied == 0
    target.add.assert_not_called()


def test_fast_copy_rejects_incomplete_metadata_before_writing() -> None:
    source = MagicMock(metadata={"embedding_dimension": 4})
    target = MagicMock(metadata={"embedding_dimension": 4})
    source.count.return_value = 2
    source.get.return_value = {
        "ids": ["chunk-1", "chunk-2"],
        "metadatas": [{"index_config_fingerprint": "active"}],
    }

    copied = _try_copy_collection_chunks(
        _client_with(source, target), "meetings", "shadow", "active"
    )

    assert copied == 0
    target.add.assert_not_called()


def test_fast_copy_raises_when_source_changes_during_copy() -> None:
    source = MagicMock(metadata={"embedding_dimension": 4})
    target = MagicMock(metadata={"embedding_dimension": 4})
    source.count.return_value = 2
    source.get.side_effect = [
        {
            "ids": ["chunk-1", "chunk-2"],
            "metadatas": [
                {"index_config_fingerprint": "active"},
                {"index_config_fingerprint": "active"},
            ],
        },
        {
            "ids": ["chunk-1"],
            "embeddings": [[1.0, 0.0, 0.0, 0.0]],
            "documents": ["one"],
            "metadatas": [{"index_config_fingerprint": "active"}],
        },
        {"ids": []},
    ]

    with pytest.raises(RuntimeError, match="1/2 chunks copied"):
        _try_copy_collection_chunks(_client_with(source, target), "meetings", "shadow", "active")


@pytest.mark.parametrize("missing_shadow", [False, True])
def test_real_chroma_generation_swap_and_rollback(tmp_path, missing_shadow):
    import chromadb

    from src.api.routers.settings._rebuild import _swap_vector_collections

    client = chromadb.PersistentClient(path=str(tmp_path / "swap"))
    live = client.create_collection("meetings")
    live.add(ids=["old"], embeddings=[[1.0, 0.0]], documents=["old evidence"])
    if missing_shadow:
        with pytest.raises(chromadb.errors.NotFoundError):
            _swap_vector_collections(client, "meetings_shadow_test", "meetings_retired")
        assert client.get_collection("meetings").get()["ids"] == ["old"]
    else:
        shadow = client.create_collection("meetings_shadow_test")
        shadow.add(ids=["new"], embeddings=[[0.0, 1.0]], documents=["new evidence"])
        assert _swap_vector_collections(client, shadow.name, "meetings_retired")
        assert client.get_collection("meetings").get()["ids"] == ["new"]
        assert client.get_collection("meetings_retired").get()["ids"] == ["old"]
