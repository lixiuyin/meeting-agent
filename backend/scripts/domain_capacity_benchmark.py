"""Isolated SQLite history/query soak. No production data and no model calls.

Run: python -m scripts.domain_capacity_benchmark --versions 10000 100000 --seconds 60
This measures a component, not end-to-end chat or multi-replica availability.
"""

import argparse
import json
import math
import sqlite3
import tempfile
import time
from pathlib import Path


def run(versions: int, seconds: float) -> dict:
    from src.core.database import SCHEMA_SQL, search_structured_memories

    with tempfile.TemporaryDirectory(prefix="meeting-domain-capacity-") as directory:
        path = Path(directory) / "capacity.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_SQL)
        facts = max(1, versions // 10)
        conn.executemany(
            "INSERT INTO user_memories(user_id,key,value,source,fact_type) "
            "VALUES('capacity',?,'work','manual','action_item')",
            [(f"task.{i:06d}",) for i in range(facts)],
        )
        conn.executemany(
            "INSERT INTO memory_fact_versions(memory_id,user_id,memory_key,revision,value,"
            "source,fact_type,action_status,valid_from,recorded_at) "
            "VALUES(?,'capacity',?,?,'work','manual','action_item','open',?,?)",
            [
                (i + 1, f"task.{i:06d}", revision, "2026-01-01", f"2026-02-{revision:02d}")
                for i in range(facts)
                for revision in range(1, 11)
            ],
        )
        conn.commit()
        latencies = []
        started = time.monotonic()
        while time.monotonic() - started < seconds or not latencies:
            before = time.monotonic()
            rows, total = search_structured_memories(
                conn, user_id="capacity", fact_types=["action_item"], as_of="2026-03-01", limit=25
            )
            assert total == facts and len(rows) == min(25, facts)
            assert all(row["revision"] == 10 for row in rows)
            latencies.append(time.monotonic() - before)
        conn.close()
        # Reopen proves persisted data and a clean local recovery path.
        with sqlite3.connect(path) as reopened:
            integrity = reopened.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok"
        ordered = sorted(latencies)
        return {
            "component": "sqlite_history_no_llm",
            "versions": facts * 10,
            "queries": len(ordered),
            "seconds": time.monotonic() - started,
            "p95_seconds": ordered[math.ceil(len(ordered) * 0.95) - 1],
            "p99_seconds": ordered[math.ceil(len(ordered) * 0.99) - 1],
            "integrity": integrity,
            "end_to_end_chat_evaluated": False,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versions", type=int, nargs="+", default=[10000, 100000])
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    if (
        args.seconds <= 0
        or args.seconds > 86400
        or any(n < 10 or n > 1000000 for n in args.versions)
    ):
        parser.error("Use 10-1000000 versions and 0-86400 seconds")
    for count in args.versions:
        print(json.dumps(run(count, args.seconds)), flush=True)
