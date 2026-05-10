"""One-shot migration: backfill ``chunk_id`` into BM25 metadata JSON.

Before the R-H1 fix, BM25 metadata was written without a ``chunk_id`` field,
which caused RRF dedup key mismatches between the vector and BM25 retrieval
paths.  This script reads every row in ``bm25_index``, synthesises the
``chunk_id`` from ``meeting_id`` + metadata ``file_id`` + ``chunk_index``,
and writes the updated JSON back.

Usage::

    uv run python -m scripts.migrate_bm25_metadata [--dry-run]
"""

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "meetings.db"


def _chunk_id_prefix(meeting_id: int, file_id: int | None) -> str:
    if file_id is None:
        return f"meeting_{meeting_id}"
    return f"meeting_{meeting_id}_file_{file_id}"


def migrate(dry_run: bool = False) -> int:
    """Backfill chunk_id into bm25_index metadata. Returns updated count."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT id, meeting_id, chunk_id, metadata FROM bm25_index").fetchall()
    updated = 0
    for row in rows:
        try:
            meta = json.loads(row["metadata"])
        except (json.JSONDecodeError, TypeError):
            continue

        # Already has chunk_id — skip
        if meta.get("chunk_id"):
            continue

        existing_chunk_id = row["chunk_id"]  # from the DB column (may be empty/legacy)
        file_id = meta.get("file_id")
        chunk_index = meta.get("chunk_index", 0)

        # Best-effort: prefer the DB column if it looks well-formed, otherwise
        # synthesise from meeting_id + file_id + chunk_index.
        if existing_chunk_id and "_chunk_" in str(existing_chunk_id):
            meta["chunk_id"] = existing_chunk_id
        else:
            prefix = _chunk_id_prefix(row["meeting_id"], file_id)
            meta["chunk_id"] = f"{prefix}_chunk_{chunk_index}"

        if not dry_run:
            conn.execute(
                "UPDATE bm25_index SET metadata = ? WHERE id = ?",
                (json.dumps(meta), row["id"]),
            )
        updated += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill chunk_id into BM25 metadata")
    parser.add_argument("--dry-run", action="store_true", help="Count changes without writing")
    args = parser.parse_args()

    if not DB_PATH.exists():
        logger.error("Database not found at %s", DB_PATH)
        sys.exit(1)

    updated = migrate(dry_run=args.dry_run)
    action = "Would update" if args.dry_run else "Updated"
    logger.info("%s %d BM25 metadata rows", action, updated)


if __name__ == "__main__":
    main()
