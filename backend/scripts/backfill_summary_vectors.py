"""Index every existing meeting_files.summary into the summary vector collection.

Idempotent: re-running upserts the same doc IDs (`file_{file_id}`) so the
collection converges to the current DB state. Files with empty / null
summaries are skipped (and have any stale vector removed).

Usage:
    uv run python -m scripts.backfill_summary_vectors
    uv run python -m scripts.backfill_summary_vectors --batch-size 50 --dry-run
    uv run python -m scripts.backfill_summary_vectors --meeting-id 42

Run this once after deploying Plan B before flipping
``rag.summary_router_enabled`` to true.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill summary vectors from meeting_files table",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Files processed per DB read (default: 100)",
    )
    parser.add_argument(
        "--meeting-id",
        type=int,
        default=None,
        help="Only backfill files for this meeting id",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows that would be indexed without writing vectors",
    )
    parser.add_argument(
        "--purge-stale",
        action="store_true",
        help="After upserting, drop summary vectors whose file no longer exists in DB",
    )
    return parser


def _load_files(batch_size: int, meeting_id: int | None) -> list[dict[str, Any]]:
    from src.core.database import get_connection

    sql = (
        "SELECT id, meeting_id, file_name, file_type, summary "
        "FROM meeting_files "
        "WHERE summary IS NOT NULL AND TRIM(summary) != ''"
    )
    params: list[Any] = []
    if meeting_id is not None:
        sql += " AND meeting_id = ?"
        params.append(meeting_id)
    sql += " ORDER BY id"

    rows: list[dict[str, Any]] = []
    with get_connection() as conn:
        cursor = conn.execute(sql, params)
        while True:
            chunk = cursor.fetchmany(batch_size)
            if not chunk:
                break
            for row in chunk:
                rows.append(dict(row))
    return rows


def _purge_stale(known_ids: set[int]) -> int:
    """Drop summary vectors whose file_id is no longer in the DB."""
    from src.services.rag._summary_vectorstore import (
        get_summary_vectorstore,
        summary_vectorstore_write_lock,
    )

    store = get_summary_vectorstore()
    try:
        payload = store._collection.get(include=["metadatas"])
    except Exception:
        logger.warning("Could not enumerate summary collection; skipping purge", exc_info=True)
        return 0

    stale_ids: list[str] = []
    for doc_id, meta in zip(payload.get("ids") or [], payload.get("metadatas") or [], strict=False):
        fid = (meta or {}).get("file_id") if isinstance(meta, dict) else None
        if isinstance(fid, int) and fid not in known_ids:
            stale_ids.append(doc_id)

    if not stale_ids:
        return 0
    with summary_vectorstore_write_lock():
        store._collection.delete(ids=stale_ids)
    return len(stale_ids)


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from src.core.database import init_db

    init_db()

    rows = _load_files(args.batch_size, args.meeting_id)
    logger.info("Backfill candidates: %d files with non-empty summary", len(rows))

    if args.dry_run:
        for row in rows[:10]:
            logger.info(
                "  file_id=%d meeting_id=%d name=%r summary_len=%d",
                row["id"],
                row["meeting_id"],
                row.get("file_name"),
                len(row.get("summary") or ""),
            )
        if len(rows) > 10:
            logger.info("  ... %d more", len(rows) - 10)
        return 0

    from src.services.rag._summary_vectorstore import upsert_file_summary

    indexed = 0
    failures = 0
    known_ids: set[int] = set()
    for row in rows:
        try:
            upsert_file_summary(
                row["id"],
                row["summary"],
                meeting_id=row["meeting_id"],
                file_name=row.get("file_name"),
                file_type=row.get("file_type"),
            )
            known_ids.add(row["id"])
            indexed += 1
            if indexed % 50 == 0:
                logger.info("Indexed %d / %d", indexed, len(rows))
        except Exception:
            logger.warning("Backfill failed for file %d", row["id"], exc_info=True)
            failures += 1

    logger.info("Done. indexed=%d failures=%d", indexed, failures)

    if args.purge_stale:
        purged = _purge_stale(known_ids)
        logger.info("Purged %d stale summary vectors", purged)

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
