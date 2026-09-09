"""Process-crash recovery in an isolated DB, not a power-loss/HA certification."""

import sqlite3
import subprocess
import sys


def test_wal_aborted_writer_preserves_committed_data(tmp_path):
    path = tmp_path / "crash-fixture.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO facts VALUES (1, 'committed')")
    conn.close()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA synchronous=FULL')
conn.execute('BEGIN IMMEDIATE')
conn.execute("UPDATE facts SET value='uncommitted' WHERE id=1")
os._exit(77)
""",
            str(path),
        ],
        timeout=20,
        check=False,
    )
    assert result.returncode == 77
    with sqlite3.connect(path) as recovered:
        assert recovered.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert recovered.execute("SELECT value FROM facts").fetchone()[0] == "committed"
        recovered.execute("UPDATE facts SET value='recovered' WHERE id=1")
    recovered.close()
    with sqlite3.connect(path) as reopened:
        assert reopened.execute("SELECT value FROM facts").fetchone()[0] == "recovered"
    reopened.close()
