"""Backfill ``file_id`` and ``chunk_id`` in legacy BM25 metadata JSON.

Before the R-H1 fix, BM25 metadata was written without a ``chunk_id`` field,
which caused scoped retrieval and RRF dedup mismatches between the vector and
BM25 paths. The configured application database is used by default; an
explicit path can be supplied for backups or isolated environments.

Usage::

    uv run python -m scripts.migrate_bm25_metadata --dry-run
    uv run python -m scripts.migrate_bm25_metadata --db /path/to/meetings.db
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = Path(os.getenv("DATA_DIR", str(_REPO_ROOT / "data"))).expanduser()
DEFAULT_DB_PATH = Path(os.getenv("DB_PATH", str(_DATA_DIR / "meetings.db"))).expanduser()
_CHUNK_FILE_ID_RE = re.compile(r"^meeting_(?P<meeting_id>\d+)_file_(?P<file_id>\d+)(?:_|$)")


def _file_id_from_chunk_id(chunk_id: object, meeting_id: int) -> int | None:
    match = _CHUNK_FILE_ID_RE.match(str(chunk_id or ""))
    if not match or int(match.group("meeting_id")) != meeting_id:
        return None
    return int(match.group("file_id"))


def _chunk_id_prefix(meeting_id: int, file_id: int | None) -> str:
    if file_id is None:
        return f"meeting_{meeting_id}"
    return f"meeting_{meeting_id}_file_{file_id}"


def migrate(db_path: Path = DEFAULT_DB_PATH, dry_run: bool = False) -> int:
    """Backfill recoverable BM25 metadata fields and return the changed row count."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bm25_index'"
        ).fetchone()
        if table is None:
            raise RuntimeError(f"bm25_index table not found in {db_path}")

        rows = conn.execute("SELECT id, meeting_id, chunk_id, metadata FROM bm25_index").fetchall()
        updated = 0
        for row in rows:
            try:
                decoded = json.loads(row["metadata"])
                meta = decoded if isinstance(decoded, dict) else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}

            changed = False
            existing_chunk_id = row["chunk_id"]
            file_id = meta.get("file_id")
            if file_id is None:
                file_id = _file_id_from_chunk_id(existing_chunk_id, row["meeting_id"])
                if file_id is not None:
                    meta["file_id"] = file_id
                    changed = True

            if not meta.get("chunk_id"):
                chunk_index = meta.get("chunk_index", 0)
                meta["chunk_id"] = existing_chunk_id or (
                    f"{_chunk_id_prefix(row['meeting_id'], file_id)}_chunk_{chunk_index}"
                )
                changed = True

            if not changed:
                continue
            if not dry_run:
                conn.execute(
                    "UPDATE bm25_index SET metadata = ? WHERE id = ?",
                    (json.dumps(meta), row["id"]),
                )
            updated += 1

        if dry_run:
            conn.rollback()
        return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill file_id/chunk_id in BM25 metadata")
    parser.add_argument("--dry-run", action="store_true", help="Count changes without writing")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Database path (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    db_path = args.db.expanduser().resolve()

    if not db_path.is_file():
        logger.error("Database not found at %s", db_path)
        sys.exit(1)

    try:
        updated = migrate(db_path=db_path, dry_run=args.dry_run)
    except (RuntimeError, sqlite3.Error) as exc:
        logger.error("Migration failed: %s", exc)
        sys.exit(1)
    action = "Would update" if args.dry_run else "Updated"
    logger.info("%s %d BM25 metadata rows in %s", action, updated, db_path)


if __name__ == "__main__":
    main()
