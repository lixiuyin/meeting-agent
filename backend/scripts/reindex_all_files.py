"""Reindex all meeting_files after chunk-id scheme fix.

Clears existing Chroma vectors for all meetings, then re-processes every
file whose status is ``ready``.  Run once after the chunk ID collision fix
so that each file's vectors use unique IDs that include file_id.

Usage:
    cd backend/
    uv run python -m scripts.reindex_all_files
"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    # Local imports so the script can be run via ``python -m scripts.reindex_all_files``
    from src.core.database import get_connection
    from src.services.rag._vectorstore import get_vectorstore

    # 1. Wipe all existing vectors so stale IDs don't linger
    vectorstore = get_vectorstore()
    logger.info("Clearing all existing vectors from Chroma ...")
    try:
        vectorstore.delete(where={"meeting_id": {"$gte": 0}})
    except Exception:
        # Collection may be empty — that's fine
        logger.warning("Chroma delete failed (collection may be empty)", exc_info=True)

    # 2. Collect all ready files
    from src.core.database import get_write_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, meeting_id FROM meeting_files WHERE status = 'ready' ORDER BY id"
        ).fetchall()
    file_ids = [(r["id"], r["meeting_id"]) for r in rows]
    logger.info("Found %d ready files to reindex", len(file_ids))

    # Clear content_hash so process_meeting_file won't skip due to hash match
    with get_write_connection() as conn:
        conn.execute("UPDATE meeting_files SET content_hash = NULL")
        conn.commit()
    logger.info("Cleared content_hash for all files to force re-index")

    # 3. Re-process each file (this re-runs the full pipeline: parse → chunk → index)
    from src.services.processor._pipeline import process_meeting_file

    success = 0
    failed = 0
    for fid, mid in file_ids:
        try:
            await process_meeting_file(fid)
            success += 1
            logger.info("file %d (meeting %d) reindexed OK", fid, mid)
        except Exception as exc:
            failed += 1
            logger.error("file %d (meeting %d) FAILED: %s", fid, mid, exc)

    logger.info("Done: %d succeeded, %d failed out of %d files", success, failed, len(file_ids))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
