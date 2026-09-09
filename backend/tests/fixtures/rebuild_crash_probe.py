"""Disposable subprocess harness; environment paths are supplied by its test."""

import json
import os
import sys
from typing import Any

import chromadb

from src.core import database as db
from src.core.config import settings
from src.core.index_manifest import index_config_fingerprint
from src.core.settings_epoch import get_settings_epoch
from src.services.rag._bm25_maintenance import rebuild_bm25_from_chroma
from src.services.rag._publication import publish_generation, recover_publication, source_snapshot
from src.services.rag._reconcile import native_manifest_checksum

db.init_db()
client: Any = chromadb.PersistentClient(path=str(settings.VECTOR_DB_DIR))
phase = sys.argv[1]
if phase == "recover":
    recover_publication(client)
    with db.get_connection() as conn:
        print(
            json.dumps(
                {
                    "vectors": client.get_collection("meetings").get()["ids"],
                    "bm25": [row[0] for row in conn.execute("SELECT chunk_id FROM bm25_index")],
                    "state": list(
                        conn.execute(
                            "SELECT native_status,native_generation,repair_pending FROM index_state"
                        ).fetchone()
                    ),
                }
            )
        )
    client.close()
    db.close_all_connections()
    raise SystemExit(0)

fingerprint = index_config_fingerprint()
with db.get_write_connection() as conn:
    mid = db.create_meeting(conn, title="Crash fixture", user_id="fixture")
    fid = db.create_meeting_file(
        conn,
        meeting_id=mid,
        file_type="txt",
        file_name="fixture.txt",
        file_path="fixture.txt",
        user_id="fixture",
    )
    conn.execute(
        "UPDATE meeting_files SET status='ready',transcript='Fixture text' WHERE id=?", (fid,)
    )
    db.mark_native_index_ready(
        conn,
        file_id=fid,
        meeting_id=mid,
        indexed_at="2026-09-08",
        generation="old",
        config_fingerprint=fingerprint,
        chroma_chunk_count=1,
        bm25_chunk_count=1,
        manifest_checksum=native_manifest_checksum(["old"], ["old"]),
    )
for name, generation in (("meetings", "old"), ("meetings_shadow_crash", "new")):
    collection = client.create_collection(name)
    collection.add(
        ids=[generation],
        embeddings=[[1.0, 0.0]],
        documents=["Fixture text"],
        metadatas=[
            {
                "file_id": fid,
                "meeting_id": mid,
                "index_generation": generation,
                "index_config_fingerprint": fingerprint,
            }
        ],
    )
rebuild_bm25_from_chroma(True, strict=True, source=client.get_collection("meetings"))

if phase == "after_swap":
    from src.api.routers.settings import _rebuild

    original_swap = _rebuild._swap_vector_collections

    def interrupted_swap(*args):
        original_swap(*args)
        os._exit(97)

    _rebuild._swap_vector_collections = interrupted_swap
elif phase == "after_manifest":
    original_mark = db.mark_native_index_ready

    def interrupted_mark(*args, **kwargs):
        original_mark(*args, **kwargs)
        os._exit(97)

    db.mark_native_index_ready = interrupted_mark
elif phase == "after_commit":
    original_delete = client.delete_collection

    def interrupted_delete(name, **kwargs):
        if name == "meetings_retired":
            os._exit(97)
        return original_delete(name, **kwargs)

    client.delete_collection = interrupted_delete
else:
    raise ValueError(phase)
publish_generation(
    client,
    "meetings_shadow_crash",
    "meetings_retired",
    [{"file_id": fid, "meeting_id": mid}],
    fingerprint,
    source_snapshot(),
    get_settings_epoch(),
)
raise AssertionError("Crash failpoint was not reached")
