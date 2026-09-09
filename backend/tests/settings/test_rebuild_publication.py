"""Real SQLite/Chroma publication, rollback, recovery and cached-handle regression."""

import asyncio
import json
import threading
from datetime import UTC, datetime
from unittest.mock import patch

import chromadb
import pytest
from langchain_core.embeddings import Embeddings

from src.core import database as db
from src.core.config import settings
from src.core.index_manifest import index_config_fingerprint
from src.core.settings_epoch import get_settings_epoch
from src.services.rag._publication import (
    index_read_lease,
    publication_barrier,
    publish_generation,
    recover_publication,
    source_snapshot,
)
from src.services.rag._reconcile import native_manifest_checksum, reconcile_index_state
from src.services.rag._vectorstore import _LiveCollection, reset_vectorstore


class LocalEmbeddings(Embeddings):
    dimension = 2

    def embed_documents(self, texts):
        return [[1.0, 0.5] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.5]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    from src.services.rag._bm25_maintenance import rebuild_bm25_from_chroma

    monkeypatch.setattr(settings, "VECTOR_DB_DIR", tmp_path / "vectors")
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSION", 2)
    monkeypatch.setattr(settings, "PARENT_CHILD_ENABLED", False)
    monkeypatch.setattr(settings, "SEMANTIC_CHUNKING_ENABLED", False)
    embeddings = LocalEmbeddings()
    for module in (
        "src.services.embedder",
        "src.services.rag._vectorstore",
        "src.services.rag._indexer_store",
        "src.services.rag._indexer",
    ):
        monkeypatch.setattr(f"{module}.get_embeddings", lambda: embeddings)
    reset_vectorstore()
    client = chromadb.PersistentClient(path=str(settings.VECTOR_DB_DIR))
    with db.get_write_connection() as conn:
        mid = db.create_meeting(conn, title="Rebuild fixture", user_id="audit-user")
        fid = db.create_meeting_file(
            conn,
            meeting_id=mid,
            file_type="txt",
            file_name="audit.txt",
            file_path="fixture.txt",
            user_id="audit-user",
        )
        conn.execute(
            "UPDATE meeting_files SET status='ready',transcript=? WHERE id=?",
            ("Alice owns the milestone due Friday.", fid),
        )
    fingerprint = index_config_fingerprint()
    meta = {
        "meeting_id": mid,
        "file_id": fid,
        "user_id": "audit-user",
        "index_generation": "old",
        "index_config_fingerprint": fingerprint,
    }
    live = client.create_collection("meetings", metadata={"embedding_dimension": 2})
    live.add(ids=["old"], embeddings=[[1.0, 0.5]], documents=["Alice owns it"], metadatas=[meta])
    rebuild_bm25_from_chroma(True, strict=True, source=live)
    with db.get_write_connection() as conn:
        db.mark_native_index_ready(
            conn,
            file_id=fid,
            meeting_id=mid,
            indexed_at=datetime.now(UTC).isoformat(),
            generation="old",
            config_fingerprint=fingerprint,
            chroma_chunk_count=1,
            bm25_chunk_count=1,
            manifest_checksum=native_manifest_checksum(["old"], ["old"]),
        )
    shadow = client.create_collection("meetings_shadow_test", metadata={"embedding_dimension": 2})
    shadow.add(
        ids=["new"],
        embeddings=[[1.0, 0.5]],
        documents=["Alice owns it"],
        metadatas=[{**meta, "index_generation": "new"}],
    )
    try:
        yield client, [{"file_id": fid, "meeting_id": mid}], fingerprint
    finally:
        reset_vectorstore()
        client.close()


def publish(corpus, snapshot=None):
    client, rows, fingerprint = corpus
    publish_generation(
        client,
        "meetings_shadow_test",
        "meetings_retired",
        rows,
        fingerprint,
        snapshot or source_snapshot(),
        get_settings_epoch(),
    )


def test_publish_commits_manifests_and_rebinds_cached_handle(corpus):
    client, _, _ = corpus
    cached = _LiveCollection(client)
    cached_get = cached.get
    publish(corpus)
    assert cached_get()["ids"] == ["new"]
    cached.upsert(ids=["inflight"], embeddings=[[1.0, 0.5]], documents=["late write"])
    assert set(client.get_collection("meetings").get()["ids"]) == {"new", "inflight"}
    with db.get_connection() as conn:
        assert conn.execute("SELECT native_generation FROM index_state").fetchone()[0] == "new"
    assert "meetings_retired" not in {item.name for item in client.list_collections()}


def test_source_change_aborts_without_losing_upload(corpus):
    before = source_snapshot()
    with db.get_write_connection() as conn:
        conn.execute("UPDATE meeting_files SET transcript='Changed by user'")
    with pytest.raises(RuntimeError, match="Source data changed"):
        publish(corpus, before)
    assert corpus[0].get_collection("meetings").get()["ids"] == ["old"]


def test_manifest_commit_failure_rolls_back_all_stores(corpus):
    with patch.object(db, "mark_native_index_ready", side_effect=RuntimeError("commit fault")):
        with pytest.raises(RuntimeError, match="commit fault"):
            publish(corpus)
    assert corpus[0].get_collection("meetings").get()["ids"] == ["old"]
    with db.get_connection() as conn:
        assert conn.execute("SELECT chunk_id FROM bm25_index").fetchone()[0] == "old"
        state = conn.execute(
            "SELECT native_status,native_generation,repair_pending FROM index_state"
        ).fetchone()
        assert tuple(state) == ("ready", "old", 0)
        assert conn.execute("SELECT COUNT(*) FROM durable_jobs").fetchone()[0] == 0


@pytest.mark.parametrize("committed", [False, True])
def test_startup_respects_durable_commit_marker(corpus, committed):
    from src.api.routers.settings._rebuild import _swap_vector_collections

    client, _, _ = corpus
    _swap_vector_collections(client, "meetings_shadow_test", "meetings_retired")
    with db.get_write_connection() as conn:
        conn.execute(
            "INSERT INTO kv_state(key,value) VALUES (?,?)",
            (
                "native_index_publication",
                json.dumps(
                    {
                        "phase": "committed" if committed else "prepared",
                        "shadow": "meetings_shadow_test",
                        "retired": "meetings_retired",
                    }
                ),
            ),
        )
    recover_publication(client)
    assert client.get_collection("meetings").get()["ids"] == (["new"] if committed else ["old"])


def test_publication_waits_for_inflight_reader():
    entered, release, published = threading.Event(), threading.Event(), threading.Event()

    def reader():
        with index_read_lease():
            entered.set()
            assert release.wait(5)

    def writer():
        with publication_barrier():
            published.set()

    first = threading.Thread(target=reader)
    second = threading.Thread(target=writer)
    first.start()
    assert entered.wait(5)
    second.start()
    try:
        assert not published.wait(0.05)
    finally:
        release.set()
        first.join(5)
        second.join(5)
    assert published.is_set()


def test_full_config_change_rebuild_is_ready(corpus, monkeypatch):
    from src.api.routers.settings._common import (
        release_all_rebuild_locks,
        try_acquire_vectors_rebuild,
    )
    from src.api.routers.settings._rebuild import _rebuild_vectors_task

    # The prebuilt shadow belongs to other publication tests, not this task.
    corpus[0].delete_collection("meetings_shadow_test")
    monkeypatch.setattr(settings, "CHUNK_SIZE_TOKENS", settings.CHUNK_SIZE_TOKENS + 1)
    monkeypatch.setattr(
        "src.services.rag._summary_vectorstore.sync_missing_file_summary_vectors", lambda: 0
    )
    monkeypatch.setattr(
        "src.services.chain._meeting_summary_lifecycle.reconcile_meeting_summaries", lambda: {}
    )
    assert try_acquire_vectors_rebuild()
    try:
        asyncio.run(_rebuild_vectors_task(get_settings_epoch()))
    finally:
        release_all_rebuild_locks()
    assert reconcile_index_state()["repair_pending"] == 0
    with db.get_connection() as conn:
        assert tuple(
            conn.execute("SELECT native_status,repair_pending FROM index_state").fetchone()
        ) == ("ready", 0)
        assert conn.execute("SELECT COUNT(*) FROM durable_jobs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_cancel_drains_owned_thread_before_releasing_result():
    from src.api.routers.settings._rebuild import _owned_thread

    entered, release = threading.Event(), threading.Event()
    cleaned = []

    def work():
        entered.set()
        assert release.wait(5)
        return "client"

    task = asyncio.create_task(_owned_thread(work, on_cancel=cleaned.append))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert cleaned == []
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned == ["client"]
