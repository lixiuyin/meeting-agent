"""Native index reconciliation validates stores instead of trusting file timestamps."""

import json
from unittest.mock import MagicMock, patch

from src.core import database as db
from src.core.index_manifest import index_config_fingerprint
from src.services.rag import _reconcile as reconcile_module


def test_reconcile_marks_missing_actual_indexes_for_durable_repair() -> None:
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Manifest", user_id="principal")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="manifest.txt",
            file_path="/tmp/manifest.txt",
            user_id="principal",
        )
        db.update_meeting_file_status(conn, file_id, "ready", transcript="content")

    vectorstore = MagicMock()
    vectorstore.get.return_value = {"ids": [], "metadatas": []}
    with patch("src.services.rag._vectorstore.get_vectorstore", return_value=vectorstore):
        result = reconcile_module.reconcile_index_state()

    assert result["reconciled"] == 1
    assert result["repair_pending"] == 1
    with db.get_connection() as conn:
        state = conn.execute(
            "SELECT native_status, repair_pending FROM index_state WHERE file_id=?",
            (file_id,),
        ).fetchone()
        job = conn.execute(
            "SELECT status, payload_json FROM durable_jobs WHERE dedupe_key=?",
            (f"file:{file_id}",),
        ).fetchone()
    assert dict(state) == {"native_status": "failed", "repair_pending": 1}
    assert job["status"] == "pending"
    payload = json.loads(job["payload_json"])
    assert payload["repair_reason"] == "index_manifest_mismatch"
    assert payload["force_native_reindex"] is True


def test_reconcile_preserves_expected_manifest_when_physical_state_matches() -> None:
    generation = "generation-1"
    fingerprint = index_config_fingerprint()
    vector_ids = ["v-1", "v-2"]
    bm25_ids = ["b-1", "b-2"]
    checksum = reconcile_module.native_manifest_checksum(vector_ids, bm25_ids)
    metadata = {
        "file_id": 1,
        "index_generation": generation,
        "index_config_fingerprint": fingerprint,
    }
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Manifest", user_id="principal")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="manifest.txt",
            file_path="/tmp/manifest.txt",
            user_id="principal",
        )
        metadata["file_id"] = file_id
        db.update_meeting_file_status(conn, file_id, "ready", transcript="content")
        for chunk_id in bm25_ids:
            db.add_bm25_chunk(
                conn,
                chunk_id=chunk_id,
                meeting_id=meeting_id,
                content="content",
                metadata=json.dumps(metadata),
            )
        db.mark_native_index_ready(
            conn,
            file_id=file_id,
            meeting_id=meeting_id,
            indexed_at="2026-01-01T00:00:00+00:00",
            generation=generation,
            config_fingerprint=fingerprint,
            chroma_chunk_count=len(vector_ids),
            bm25_chunk_count=len(bm25_ids),
            manifest_checksum=checksum,
        )

    vectorstore = MagicMock()
    vectorstore.get.return_value = {
        "ids": vector_ids,
        "metadatas": [metadata.copy() for _ in vector_ids],
    }
    with patch("src.services.rag._vectorstore.get_vectorstore", return_value=vectorstore):
        result = reconcile_module.reconcile_index_state()

    assert result["repair_pending"] == 0
    with db.get_connection() as conn:
        state = conn.execute(
            "SELECT native_status, native_manifest_checksum FROM index_state WHERE file_id=?",
            (file_id,),
        ).fetchone()
    assert dict(state) == {"native_status": "ready", "native_manifest_checksum": checksum}


def test_reconcile_rejects_partial_store_instead_of_recertifying_it() -> None:
    generation = "generation-1"
    fingerprint = index_config_fingerprint()
    expected_vector_ids = ["v-1", "v-2"]
    expected_bm25_ids = ["b-1", "b-2"]
    expected_checksum = reconcile_module.native_manifest_checksum(
        expected_vector_ids, expected_bm25_ids
    )
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Manifest", user_id="principal")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="manifest.txt",
            file_path="/tmp/manifest.txt",
            user_id="principal",
        )
        metadata = {
            "file_id": file_id,
            "index_generation": generation,
            "index_config_fingerprint": fingerprint,
        }
        db.update_meeting_file_status(conn, file_id, "ready", transcript="content")
        db.add_bm25_chunk(
            conn,
            chunk_id="b-1",
            meeting_id=meeting_id,
            content="content",
            metadata=json.dumps(metadata),
        )
        db.mark_native_index_ready(
            conn,
            file_id=file_id,
            meeting_id=meeting_id,
            indexed_at="2026-01-01T00:00:00+00:00",
            generation=generation,
            config_fingerprint=fingerprint,
            chroma_chunk_count=2,
            bm25_chunk_count=2,
            manifest_checksum=expected_checksum,
        )

    vectorstore = MagicMock()
    vectorstore.get.return_value = {"ids": ["v-1"], "metadatas": [metadata]}
    with patch("src.services.rag._vectorstore.get_vectorstore", return_value=vectorstore):
        result = reconcile_module.reconcile_index_state()

    assert result["repair_pending"] == 1
    with db.get_connection() as conn:
        state = conn.execute(
            "SELECT native_status, native_manifest_checksum FROM index_state WHERE file_id=?",
            (file_id,),
        ).fetchone()
    assert dict(state) == {
        "native_status": "failed",
        "native_manifest_checksum": expected_checksum,
    }


def test_reconcile_supports_keyset_pagination() -> None:
    file_ids: list[int] = []
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Pagination", user_id="principal")
        for index in range(2):
            file_id = db.create_meeting_file(
                conn,
                meeting_id=meeting_id,
                file_type="txt",
                file_name=f"manifest-{index}.txt",
                file_path=f"/tmp/manifest-{index}.txt",
                user_id="principal",
            )
            db.update_meeting_file_status(conn, file_id, "ready", transcript="content")
            file_ids.append(file_id)

    vectorstore = MagicMock()
    vectorstore.get.return_value = {"ids": [], "metadatas": []}
    with patch("src.services.rag._vectorstore.get_vectorstore", return_value=vectorstore):
        result = reconcile_module.reconcile_index_state(limit=1, after_file_id=file_ids[0])

    assert result["reconciled"] == 1
    assert result["next_cursor"] == file_ids[1]
