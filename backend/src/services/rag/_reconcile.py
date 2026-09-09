"""Index reconciliation between meeting_files metadata and index_state."""

from __future__ import annotations

import hashlib
import json
import threading

from ...core.database import (
    enqueue_job,
    get_connection,
    get_write_connection,
    mark_native_index_repair_pending,
)
from ...core.index_manifest import index_config_fingerprint
from ._publication import consistent_index_read

_cursor_lock = threading.Lock()
_next_file_id = 0


def native_manifest_checksum(vector_ids: list[str], bm25_ids: list[str]) -> str:
    payload = json.dumps(
        {"vector": sorted(vector_ids), "bm25": sorted(bm25_ids)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _metadata_matches_generation(
    metadata: object,
    *,
    generation: str | None,
    fingerprint: str,
) -> bool:
    return (
        isinstance(metadata, dict)
        and metadata.get("index_generation") == generation
        and metadata.get("index_config_fingerprint") == fingerprint
    )


@consistent_index_read
def reconcile_index_state(*, limit: int = 500, after_file_id: int = 0) -> dict[str, int]:
    """Verify physical native indexes against their committed manifests."""
    from ._vectorstore import get_vectorstore

    if limit <= 0:
        return {"reconciled": 0, "repair_pending": 0, "next_cursor": after_file_id}

    active_fingerprint = index_config_fingerprint()
    vectorstore = get_vectorstore()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                mf.id AS file_id, mf.meeting_id, mf.raganything_doc_id,
                mf.raganything_indexed_at, ist.native_status,
                ist.native_generation, ist.native_config_fingerprint,
                ist.chroma_chunk_count, ist.bm25_chunk_count,
                ist.native_manifest_checksum
            FROM meeting_files mf
            LEFT JOIN index_state ist ON ist.file_id=mf.id
            WHERE mf.status='ready' AND mf.id>?
            ORDER BY mf.id
            LIMIT ?
            """,
            (after_file_id, limit),
        ).fetchall()

    reconciled = 0
    repair_pending = 0
    for row in rows:
        file_id = int(row["file_id"])
        meeting_id = int(row["meeting_id"])
        expected_generation = row["native_generation"]
        expected_fingerprint = row["native_config_fingerprint"]
        expected_vector_count = row["chroma_chunk_count"]
        expected_bm25_count = row["bm25_chunk_count"]
        expected_checksum = row["native_manifest_checksum"]
        try:
            vector = vectorstore.get(
                where={"$and": [{"meeting_id": meeting_id}, {"file_id": file_id}]},
                include=["metadatas"],
            )
            vector_ids = [str(value) for value in (vector.get("ids") or [])]
            vector_meta = list(vector.get("metadatas") or [])
            with get_connection() as read_conn:
                bm25_rows = read_conn.execute(
                    "SELECT chunk_id, metadata FROM bm25_index WHERE meeting_id=? "
                    "AND CAST(json_extract(metadata, '$.file_id') AS INTEGER)=?",
                    (meeting_id, file_id),
                ).fetchall()
            bm25_ids = [str(item["chunk_id"]) for item in bm25_rows]
            bm25_meta = [json.loads(item["metadata"] or "{}") for item in bm25_rows]
            expected_complete = all(
                value is not None
                for value in (
                    expected_generation,
                    expected_fingerprint,
                    expected_vector_count,
                    expected_bm25_count,
                    expected_checksum,
                )
            )
            vector_metadata_match = (
                bool(vector_ids)
                and len(vector_ids) == len(vector_meta)
                and all(
                    _metadata_matches_generation(
                        meta,
                        generation=expected_generation,
                        fingerprint=active_fingerprint,
                    )
                    for meta in vector_meta
                )
            )
            bm25_metadata_match = (
                bool(bm25_ids)
                and len(bm25_ids) == len(bm25_meta)
                and all(
                    _metadata_matches_generation(
                        meta,
                        generation=expected_generation,
                        fingerprint=active_fingerprint,
                    )
                    for meta in bm25_meta
                )
            )
            valid = (
                row["native_status"] == "ready"
                and expected_complete
                and expected_fingerprint == active_fingerprint
                and vector_metadata_match
                and bm25_metadata_match
                and len(vector_ids) == expected_vector_count
                and len(bm25_ids) == expected_bm25_count
                and native_manifest_checksum(vector_ids, bm25_ids) == expected_checksum
            )
            reason = "Native index does not match its committed generation/count/checksum manifest"
        except Exception as exc:
            valid = False
            reason = f"Native index reconciliation read failed: {type(exc).__name__}: {exc}"[:500]

        with get_write_connection() as conn:
            if not valid:
                mark_native_index_repair_pending(
                    conn,
                    file_id=file_id,
                    meeting_id=meeting_id,
                    error=reason,
                )
                enqueue_job(
                    conn,
                    kind="file_processing",
                    dedupe_key=f"file:{file_id}",
                    payload={
                        "file_id": file_id,
                        "force_native_reindex": True,
                        "repair_reason": "index_manifest_mismatch",
                    },
                    priority=10,
                )
                repair_pending += 1
            conn.execute(
                """
                INSERT INTO index_state (
                    file_id, meeting_id, raganything_doc_id,
                    raganything_indexed_at, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(file_id) DO UPDATE SET
                    meeting_id=excluded.meeting_id,
                    raganything_doc_id=COALESCE(
                        excluded.raganything_doc_id, index_state.raganything_doc_id
                    ),
                    raganything_indexed_at=COALESCE(
                        excluded.raganything_indexed_at,
                        index_state.raganything_indexed_at
                    ),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    file_id,
                    meeting_id,
                    row["raganything_doc_id"],
                    row["raganything_indexed_at"],
                ),
            )
        reconciled += 1

    return {
        "reconciled": reconciled,
        "repair_pending": repair_pending,
        "next_cursor": int(rows[-1]["file_id"]) if rows else after_file_id,
    }


def reconcile_multimodal_index_state(*, limit: int = 500) -> dict[str, int]:
    """Reconcile a rotating page of ready files against physical indexes."""
    global _next_file_id
    with _cursor_lock:
        result = reconcile_index_state(limit=limit, after_file_id=_next_file_id)
        if not result["reconciled"] and _next_file_id:
            _next_file_id = 0
            result = reconcile_index_state(limit=limit, after_file_id=0)
        _next_file_id = result["next_cursor"] if result["reconciled"] == limit else 0
        return result
