"""Quick BM25 diagnostic: verify FTS5 table and trigger work in benchmark env."""

import asyncio
import os

from _bench_env import bench_environment


def _main():
    with bench_environment():
        from src.core.config import settings
        from src.core.database import get_write_connection, init_db
        from src.core.database.bm25 import add_bm25_chunk, fts5_search

        print(f"DB_PATH: {settings.DB_PATH}")
        print(f"VECTOR_DB_DIR: {settings.VECTOR_DB_DIR}")

        init_db()

        with get_write_connection() as conn:
            # Create a dummy meeting so FK constraint is satisfied
            conn.execute(
                "INSERT INTO meetings (title, description, meeting_date) VALUES (?, ?, ?)",
                ("test", "test", "2026-01-15"),
            )
            meeting_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            print(f"Created dummy meeting id={meeting_id}")

            # Check tables exist
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bm25%'"
            ).fetchall()
            print("BM25 tables:", [r["name"] for r in tables])

            # Insert a test chunk
            add_bm25_chunk(
                conn,
                chunk_id="test_chunk_1",
                meeting_id=meeting_id,
                content="Heather is the project manager for the remote control team.",
                tokenized="[]",
                metadata=f'{{"meeting_id": {meeting_id}, "file_id": 1}}',
            )

            # Count rows
            count_idx = conn.execute("SELECT COUNT(*) AS c FROM bm25_index").fetchone()["c"]
            count_fts = conn.execute("SELECT COUNT(*) AS c FROM bm25_chunks").fetchone()["c"]
            print(f"bm25_index rows: {count_idx}")
            print(f"bm25_chunks rows: {count_fts}")

            # Search
            results = fts5_search(conn, "Heather", meeting_ids=[meeting_id], limit=5)
            print(f"FTS5 search results for 'Heather': {len(results)} rows")
            for r in results:
                print("  -", r["chunk_id"], r["content"][:60])

            # Search with phrase query
            results2 = fts5_search(
                conn, 'remote control', meeting_ids=[meeting_id], limit=5
            )
            print(f"FTS5 search results for 'remote control': {len(results2)} rows")

            # Empty meeting filter
            results3 = fts5_search(conn, "Heather", meeting_ids=[999], limit=5)
            print(f"FTS5 search results for 'Heather' meeting=999: {len(results3)} rows")


if __name__ == "__main__":
    _main()
