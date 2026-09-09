import sqlite3
from contextlib import closing

from scripts._frozen_corpus import corpus_identity, frozen_corpus


def test_cases_share_fixed_baseline_even_if_original_source_changes(tmp_path):
    source = tmp_path / "source.db"
    vectors = tmp_path / "vectors"
    vectors.mkdir()
    (vectors / "index.bin").write_bytes(b"v1")
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE facts(value TEXT)")
        conn.execute("INSERT INTO facts VALUES('original')")
    conn.close()
    with frozen_corpus(source, vectors) as (baseline, index):
        before = corpus_identity(baseline, index)
        with sqlite3.connect(source) as conn:
            conn.execute("UPDATE facts SET value='later'")
        conn.close()
        (vectors / "index.bin").write_bytes(b"v2")
        assert corpus_identity(baseline, index) == before
        assert corpus_identity(source, vectors) != before
        with closing(sqlite3.connect(baseline)) as conn:
            assert conn.execute("SELECT value FROM facts").fetchone()[0] == "original"
    assert not baseline.exists()


def test_repeated_corpus_fingerprints_close_every_connection(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    vectors = tmp_path / "vectors"
    vectors.mkdir()
    sqlite3.connect(source).close()
    real_connect = sqlite3.connect
    opened, closed = [], []

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            closed.append(self)
            super().close()

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs, factory=TrackedConnection)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    for _ in range(100):
        corpus_identity(source, vectors)
    assert len(opened) == len(closed) == 100
