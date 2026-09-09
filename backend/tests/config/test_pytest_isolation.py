"""A preloaded application must never be silently adopted by pytest cleanup."""

import contextlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def test_preloaded_non_test_database_is_rejected_without_modification(tmp_path):
    database = tmp_path / "application.db"
    with contextlib.closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE sentinel (value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('preserve')")
    before = database.read_bytes()
    env = {
        **os.environ,
        "MEETING_AGENT_DISABLE_DOTENV": "1",
        "DATA_DIR": str(tmp_path),
        "DB_PATH": str(database),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "VECTOR_DB_DIR": str(tmp_path / "vectors"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import src.core.config; import pytest; "
            "raise SystemExit(pytest.main(['--collect-only', '-q', 'tests/config/test_config.py']))",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Refusing to run tests against non-owned application paths" in result.stderr
    assert database.read_bytes() == before


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS Seatbelt validation; other hosts require isolated containers",
)
def test_os_protection_denies_sqlite_writes_through_symlink(tmp_path):
    protected = tmp_path / "source"
    protected.mkdir()
    database = protected / "canary.db"
    with contextlib.closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE sentinel(value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('preserve')")
    link = tmp_path / "alias"
    link.symlink_to(protected, target_is_directory=True)
    before = database.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[3] / "scripts/run-protected.py"),
            "--protect",
            str(protected),
            "--",
            sys.executable,
            "-c",
            "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('DELETE FROM sentinel'); c.commit()",
            str(link / "canary.db"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert database.read_bytes() == before
