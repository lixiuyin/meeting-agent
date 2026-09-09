"""Single-instance publication barrier and crash-recoverable native index commit.

Remote embeddings run outside this barrier and outside SQLite's write lock.
Only publication drains readers, checks the source snapshot, then commits local
stores. SQLite rolls back BM25/manifests after a crash; the durable journal tells
startup whether Chroma must also roll back or the retired collection can go.
"""

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from ...core import database as db
from ...core.database._connection import _write_lock

_condition = threading.Condition()
_readers = 0
_publishing = False
_local = threading.local()
_JOURNAL_KEY = "native_index_publication"


@contextmanager
def index_read_lease():
    global _readers
    nested = getattr(_local, "reading", False)
    if not nested:
        with _condition:
            _condition.wait_for(lambda: not _publishing)
            _readers += 1
        _local.reading = True
    try:
        yield
    finally:
        if not nested:
            _local.reading = False
            with _condition:
                _readers -= 1
                _condition.notify_all()


def consistent_index_read(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with index_read_lease():
            return function(*args, **kwargs)

    return wrapped


@contextmanager
def publication_barrier():
    global _publishing
    with _condition:
        _condition.wait_for(lambda: not _publishing)
        _publishing = True
        try:
            if not _condition.wait_for(lambda: _readers == 0, timeout=60):
                raise TimeoutError("Index publication timed out draining active readers")
        except BaseException:
            _publishing = False
            _condition.notify_all()
            raise
    try:
        with _write_lock:
            yield
    finally:
        with _condition:
            _publishing = False
            _condition.notify_all()


def source_snapshot() -> str:
    """Detect uploads, deletions, renames and index jobs during shadow building."""
    digest = hashlib.sha256()
    with db.get_connection() as conn:
        for query in (
            "SELECT * FROM meeting_files ORDER BY id",
            "SELECT id,title,meeting_date,user_id FROM meetings ORDER BY id",
            "SELECT * FROM index_state ORDER BY file_id",
        ):
            digest.update(query.encode())
            for row in conn.execute(query):
                digest.update(json.dumps(dict(row), sort_keys=True, default=str).encode())
                digest.update(b"\n")
    return digest.hexdigest()


def _write_journal(conn, value: dict) -> None:
    conn.execute(
        "INSERT INTO kv_state(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
        (_JOURNAL_KEY, json.dumps(value)),
    )


def publish_generation(
    client: Any,
    shadow: str,
    retired: str,
    rows: list,
    fingerprint: str,
    expected_snapshot: str,
    epoch: int,
) -> None:
    from ...api.routers.settings._rebuild import _swap_vector_collections
    from ...core.index_manifest import index_config_fingerprint
    from ...core.settings_epoch import get_settings_epoch
    from ._bm25_maintenance import rebuild_bm25_from_chroma
    from ._reconcile import native_manifest_checksum
    from ._vectorstore import reset_vectorstore

    with publication_barrier():
        if epoch != get_settings_epoch() or fingerprint != index_config_fingerprint():
            raise RuntimeError("Settings changed before index publication; retry rebuild")
        if source_snapshot() != expected_snapshot:
            raise RuntimeError(
                "Source data changed during rebuild; retry without discarding new writes"
            )
        with db.get_connection() as conn:
            if conn.execute(
                "SELECT 1 FROM index_state WHERE native_status='building' LIMIT 1"
            ).fetchone():
                raise RuntimeError(
                    "File indexing is still active; retry rebuild after it completes"
                )
        prepared = {"phase": "prepared", "shadow": shadow, "retired": retired}
        with db.get_write_connection() as conn:
            _write_journal(conn, prepared)
        swapped = False
        had_live = False
        try:
            with db.get_write_connection() as conn:
                had_live = _swap_vector_collections(client, shadow, retired)
                swapped = True
                live = client.get_collection("meetings")
                rebuild_bm25_from_chroma(True, strict=True, source=live)
                total = 0
                for row in rows:
                    data = live.get(where={"file_id": row["file_id"]}, include=["metadatas"])
                    ids, metadata = data["ids"], data["metadatas"]
                    generations = {meta.get("index_generation") for meta in metadata}
                    if not ids or len(generations) != 1 or None in generations:
                        raise RuntimeError(f"Incomplete generation for file {row['file_id']}")
                    if any(
                        meta.get("index_config_fingerprint") != fingerprint for meta in metadata
                    ):
                        raise RuntimeError("Rebuilt generation has stale configuration")
                    child_ids = {
                        key
                        for key, meta in zip(ids, metadata, strict=True)
                        if meta.get("chunk_type") != "parent"
                    }
                    bm25_ids = [
                        item[0]
                        for item in conn.execute(
                            "SELECT chunk_id FROM bm25_index WHERE "
                            "CAST(json_extract(metadata,'$.file_id') AS INTEGER)=?",
                            (row["file_id"],),
                        ).fetchall()
                    ]
                    if not child_ids or child_ids != set(bm25_ids):
                        raise RuntimeError("Rebuilt vector/BM25 child IDs do not agree")
                    db.mark_native_index_ready(
                        conn,
                        file_id=row["file_id"],
                        meeting_id=row["meeting_id"],
                        indexed_at=datetime.now(UTC).isoformat(),
                        generation=generations.pop(),
                        config_fingerprint=fingerprint,
                        chroma_chunk_count=len(ids),
                        bm25_chunk_count=len(bm25_ids),
                        manifest_checksum=native_manifest_checksum(ids, bm25_ids),
                    )
                    total += len(ids)
                if total != live.count():
                    raise RuntimeError(
                        "Shadow index contains chunks outside the ready-file snapshot"
                    )
                _write_journal(conn, {**prepared, "phase": "committed"})
        except BaseException:
            if swapped:
                client.get_collection("meetings").modify(name=shadow)
                if had_live:
                    client.get_collection(retired).modify(name="meetings")
            reset_vectorstore()
            # Keep a prepared journal if rollback itself fails, for startup recovery.
            with db.get_write_connection() as conn:
                conn.execute("DELETE FROM kv_state WHERE key=?", (_JOURNAL_KEY,))
            raise
        reset_vectorstore()
        # All registered readers drained; live wrappers also rebind by collection name.
        if had_live:
            client.delete_collection(retired)
        with db.get_write_connection() as conn:
            conn.execute("DELETE FROM kv_state WHERE key=?", (_JOURNAL_KEY,))


def recover_publication(client: Any) -> None:
    """Run before serving requests or starting file workers."""
    from ._bm25_maintenance import rebuild_bm25_from_chroma
    from ._vectorstore import reset_vectorstore

    with db.get_connection() as conn:
        row = conn.execute("SELECT value FROM kv_state WHERE key=?", (_JOURNAL_KEY,)).fetchone()
    journal = json.loads(row[0]) if row else None
    names = {collection.name for collection in client.list_collections()}
    retired = "meetings_retired"
    if retired in names:
        if journal and journal["phase"] == "committed":
            client.delete_collection(retired)
        else:
            # No committed SQLite record: never discard the last confirmed generation.
            if "meetings" in names:
                abandoned = journal["shadow"] if journal else "meetings_shadow_recovered"
                client.get_collection("meetings").modify(name=abandoned)
            client.get_collection(retired).modify(name="meetings")
            reset_vectorstore()
            if journal is None:  # Old releases did not transactionally publish BM25.
                rebuild_bm25_from_chroma(
                    True, strict=True, source=client.get_collection("meetings")
                )
    elif (
        journal
        and journal["phase"] == "prepared"
        and journal["shadow"] not in names
        and "meetings" in names
    ):
        # No old generation existed, or failure happened before the first rename.
        # Only remove live if shadow was consumed by the swap.
        client.get_collection("meetings").modify(name=journal["shadow"])
    for collection in client.list_collections():
        if collection.name.startswith("meetings_shadow_"):
            client.delete_collection(collection.name)
    with db.get_write_connection() as conn:
        conn.execute("DELETE FROM kv_state WHERE key=?", (_JOURNAL_KEY,))


def recover_persistent_publication() -> None:
    import chromadb

    from ...core.chroma_security import validate_chroma_runtime

    client: Any = chromadb.PersistentClient(path=str(validate_chroma_runtime()))
    try:
        recover_publication(client)
    finally:
        client.close()
