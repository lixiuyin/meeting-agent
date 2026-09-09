"""Regression coverage for BM25 legacy-metadata detection and repair."""

import json
import sqlite3

import pytest

from scripts.migrate_bm25_metadata import migrate
from src.core.database import add_bm25_chunk, get_connection, get_write_connection
from src.services.rag import _indexer_store


class _EmptyVectorStore:
    def get(self, *, include):
        assert include == ["metadatas"]
        return {"metadatas": []}


@pytest.mark.unit
def test_valid_json_object_is_not_reported_as_missing_file_id(monkeypatch):
    monkeypatch.setattr(_indexer_store, "get_vectorstore", lambda: _EmptyVectorStore())
    with get_write_connection() as conn:
        conn.execute("INSERT INTO meetings (id, title) VALUES (81, 'metadata test')")
        add_bm25_chunk(
            conn,
            chunk_id="meeting_81_file_17_chunk_0",
            meeting_id=81,
            content="valid metadata",
            metadata=json.dumps({"file_id": 17, "chunk_id": "meeting_81_file_17_chunk_0"}),
        )

    assert _indexer_store.count_legacy_chunks_without_file_id()["bm25"] == 0


@pytest.mark.unit
def test_runtime_backfill_recovers_file_and_chunk_ids(monkeypatch):
    monkeypatch.setattr(_indexer_store, "get_vectorstore", lambda: _EmptyVectorStore())
    chunk_id = "meeting_82_file_19_generation_deadbeef_parent_0"
    with get_write_connection() as conn:
        conn.execute("INSERT INTO meetings (id, title) VALUES (82, 'backfill test')")
        conn.execute("DELETE FROM bm25_stats WHERE key='legacy_metadata_v2_last_indexed_id'")
        add_bm25_chunk(
            conn,
            chunk_id=chunk_id,
            meeting_id=82,
            content="legacy metadata",
            metadata=json.dumps({"chunk_index": 0}),
        )

    assert _indexer_store.count_legacy_chunks_without_file_id()["bm25"] == 1
    assert _indexer_store._backfill_legacy_bm25_metadata() == 1
    assert _indexer_store.count_legacy_chunks_without_file_id()["bm25"] == 0

    with get_connection() as conn:
        row = conn.execute(
            "SELECT metadata FROM bm25_index WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
    metadata = json.loads(row["metadata"])
    assert metadata["file_id"] == 19
    assert metadata["chunk_id"] == chunk_id


@pytest.mark.unit
def test_one_shot_migration_uses_explicit_database_and_dry_run_is_read_only(tmp_path):
    db_path = tmp_path / "meetings.db"
    chunk_id = "meeting_9_file_4_child_0_1"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE bm25_index ("
            "id INTEGER PRIMARY KEY, chunk_id TEXT, meeting_id INTEGER, metadata TEXT)"
        )
        conn.execute(
            "INSERT INTO bm25_index (chunk_id, meeting_id, metadata) VALUES (?, ?, ?)",
            (chunk_id, 9, json.dumps({"chunk_index": 1})),
        )

    assert migrate(db_path=db_path, dry_run=True) == 1
    with sqlite3.connect(db_path) as conn:
        assert json.loads(conn.execute("SELECT metadata FROM bm25_index").fetchone()[0]) == {
            "chunk_index": 1
        }

    assert migrate(db_path=db_path) == 1
    with sqlite3.connect(db_path) as conn:
        metadata = json.loads(conn.execute("SELECT metadata FROM bm25_index").fetchone()[0])
    assert metadata == {"chunk_index": 1, "file_id": 4, "chunk_id": chunk_id}
