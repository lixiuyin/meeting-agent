from argparse import Namespace
from pathlib import Path

from scripts._holdout_identity import run_identity


def test_fingerprint_invalidates_config_code_and_vector_changes(tmp_path, db_conn):
    class Config:
        weight = 1

        def model_dump(self, **kwargs):
            return {"RAG_WEIGHT": self.weight, "LLM_API_KEY": "never-persist"}

    config = Config()
    args = Namespace(
        source_db=Path(db_conn.execute("PRAGMA database_list").fetchone()[2]),
        source_vector_dir=tmp_path / "vectors",
        top_k=5,
    )
    baseline = run_identity(args, config, tmp_path, "holdout")
    assert run_identity(args, config, tmp_path, "holdout") == baseline
    args.top_k = 9
    assert run_identity(args, config, tmp_path, "holdout") != baseline
    args.top_k = 5
    config.weight = 2
    assert run_identity(args, config, tmp_path, "holdout") != baseline
    config.weight = 1
    args.source_vector_dir.mkdir()
    vector = args.source_vector_dir / "index.bin"
    vector.write_bytes(b"v1")
    vector_baseline = run_identity(args, config, tmp_path, "holdout")
    vector.write_bytes(b"v2")
    assert run_identity(args, config, tmp_path, "holdout") != vector_baseline
    code = tmp_path / "src"
    code.mkdir()
    module = code / "policy.py"
    module.write_text("policy = 1")
    code_baseline = run_identity(args, config, tmp_path, "holdout")
    module.write_text("policy = 2")
    assert run_identity(args, config, tmp_path, "holdout") != code_baseline


def test_repeated_fingerprint_reads_close_database_connections(tmp_path, db_conn, monkeypatch):
    import sqlite3

    import pytest

    import scripts._holdout_identity as identity

    class Config:
        def model_dump(self, **kwargs):
            return {}

    original_connect = sqlite3.connect
    connections = []

    def tracked_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        connections.append(conn)
        return conn

    args = Namespace(
        source_db=Path(db_conn.execute("PRAGMA database_list").fetchone()[2]),
        source_vector_dir=tmp_path / "vectors",
    )
    monkeypatch.setattr(identity.sqlite3, "connect", tracked_connect)
    for _ in range(5):
        run_identity(args, Config(), tmp_path, "holdout")
    try:
        for conn in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                conn.execute("SELECT 1")
    finally:
        for conn in connections:
            conn.close()
