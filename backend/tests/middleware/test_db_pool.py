import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.core.database import _connection as pool


def test_close_all_connections_closes_worker_connections(monkeypatch, tmp_path):
    db_path = tmp_path / "pool.db"
    monkeypatch.setattr(pool.settings, "DB_PATH", db_path)

    conns: list[sqlite3.Connection] = []

    def _open_and_return_conn() -> sqlite3.Connection:
        conn = pool._get_thread_conn()
        conn.execute("SELECT 1").fetchone()
        return conn

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_open_and_return_conn) for _ in range(8)]
        conns.extend(f.result() for f in futures)

    main_conn = pool._get_thread_conn()
    conns.append(main_conn)

    pool.close_all_connections()

    for conn in conns:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1").fetchone()
