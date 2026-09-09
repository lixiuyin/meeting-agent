"""Tests for file-level summary BM25 index and hybrid routing."""

import pytest


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    """Set up a temp database with the file_summary_bm25 schema."""
    monkeypatch.setattr("src.core.constants.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("src.core.constants.DB_PATH", str(tmp_path / "test.db"))
    from src.core.database import get_connection, init_db

    init_db()
    with get_connection() as conn:
        conn.execute("INSERT INTO meetings (id, title, status) VALUES (1, 'test', 'ready')")
        conn.execute(
            "INSERT INTO meeting_files (id, meeting_id, file_name, file_path, file_type, status) "
            "VALUES (10, 1, 'a.pdf', 'a.pdf', 'pdf', 'ready')"
        )
        conn.execute(
            "INSERT INTO meeting_files (id, meeting_id, file_name, file_path, file_type, status) "
            "VALUES (20, 1, 'b.pdf', 'b.pdf', 'pdf', 'ready')"
        )
        conn.commit()
        yield conn


class TestFileSummaryBM25CRUD:
    """Test upsert, delete, search for file summary BM25."""

    def test_upsert_and_search(self, db_conn):
        from src.core.database.bm25 import (
            fts5_search_file_summaries,
            upsert_file_summary_bm25,
        )

        upsert_file_summary_bm25(db_conn, 10, 1, "Quarterly budget review with revenue targets")
        upsert_file_summary_bm25(db_conn, 20, 1, "Technical architecture design document")
        db_conn.commit()

        results = fts5_search_file_summaries(db_conn, "budget", limit=5)
        assert len(results) >= 1
        assert results[0]["file_id"] == 10
        assert results[0]["meeting_id"] == 1

    def test_cjk_query_matches_unsegmented_summary(self, db_conn):
        from src.core.database.bm25 import (
            fts5_search_file_summaries,
            upsert_file_summary_bm25,
        )

        upsert_file_summary_bm25(db_conn, 10, 1, "讨论长期记忆架构与冲突消解策略")
        upsert_file_summary_bm25(db_conn, 20, 1, "季度市场预算复盘")
        db_conn.commit()

        results = fts5_search_file_summaries(db_conn, "记忆架构", limit=5)
        assert results
        assert results[0]["file_id"] == 10

    def test_delete(self, db_conn):
        from src.core.database.bm25 import (
            delete_file_summary_bm25,
            fts5_search_file_summaries,
            upsert_file_summary_bm25,
        )

        upsert_file_summary_bm25(db_conn, 10, 1, "budget review")
        db_conn.commit()
        delete_file_summary_bm25(db_conn, 10)
        db_conn.commit()

        results = fts5_search_file_summaries(db_conn, "budget", limit=5)
        assert len(results) == 0

    def test_update_replaces(self, db_conn):
        from src.core.database.bm25 import (
            fts5_search_file_summaries,
            upsert_file_summary_bm25,
        )

        upsert_file_summary_bm25(db_conn, 10, 1, "old summary about budget")
        db_conn.commit()
        upsert_file_summary_bm25(db_conn, 10, 1, "new summary about marketing")
        db_conn.commit()

        results = fts5_search_file_summaries(db_conn, "marketing", limit=5)
        assert len(results) >= 1
        assert results[0]["file_id"] == 10

        results_old = fts5_search_file_summaries(db_conn, "budget", limit=5)
        assert not any(r["file_id"] == 10 for r in results_old)

    def test_empty_summary_deletes(self, db_conn):
        from src.core.database.bm25 import (
            fts5_search_file_summaries,
            upsert_file_summary_bm25,
        )

        upsert_file_summary_bm25(db_conn, 10, 1, "budget review")
        db_conn.commit()
        upsert_file_summary_bm25(db_conn, 10, 1, "")
        db_conn.commit()

        results = fts5_search_file_summaries(db_conn, "budget", limit=5)
        assert len(results) == 0

    def test_search_with_meeting_filter(self, db_conn):
        from src.core.database.bm25 import (
            fts5_search_file_summaries,
            upsert_file_summary_bm25,
        )

        db_conn.execute("INSERT INTO meetings (id, title, status) VALUES (2, 'm2', 'ready')")
        db_conn.execute(
            "INSERT INTO meeting_files (id, meeting_id, file_name, file_path, file_type, status) "
            "VALUES (30, 2, 'c.pdf', 'c.pdf', 'pdf', 'ready')"
        )
        db_conn.commit()
        upsert_file_summary_bm25(db_conn, 10, 1, "budget plan for Q1")
        upsert_file_summary_bm25(db_conn, 30, 2, "budget plan for Q2")
        db_conn.commit()

        results = fts5_search_file_summaries(db_conn, "budget", meeting_ids=[1], limit=5)
        assert all(r["meeting_id"] == 1 for r in results)

    def test_search_isolates_default_principal(self, db_conn):
        from src.core.database.bm25 import (
            fts5_search_file_summaries,
            upsert_file_summary_bm25,
        )

        db_conn.execute(
            "INSERT INTO meetings (id, title, status, user_id) "
            "VALUES (2, 'private', 'ready', 'principal-b')"
        )
        db_conn.execute(
            "INSERT INTO meeting_files "
            "(id, meeting_id, file_name, file_path, file_type, status, user_id) "
            "VALUES (30, 2, 'private.pdf', 'private.pdf', 'pdf', 'ready', 'principal-b')"
        )
        upsert_file_summary_bm25(db_conn, 10, 1, "shared keyword default document")
        upsert_file_summary_bm25(db_conn, 30, 2, "shared keyword private document")
        db_conn.commit()

        results = fts5_search_file_summaries(
            db_conn,
            "shared keyword",
            limit=10,
            user_id="default",
        )
        assert [row["file_id"] for row in results] == [10]


class TestRRFFusion:
    """Test the RRF fusion logic in the summary router."""

    def test_fuse_vector_and_bm25(self):
        from src.services.rag._summary_router import _rrf_fuse_file_lists

        vector = [(10, 0.9), (20, 0.7), (30, 0.3)]
        bm25 = [
            {"file_id": 20, "meeting_id": 1, "score": 5.0},
            {"file_id": 40, "meeting_id": 1, "score": 3.0},
        ]
        fused = _rrf_fuse_file_lists(vector, bm25, alpha=0.6, top_k=10)
        assert len(fused) == 4
        file_ids = [fid for fid, _ in fused]
        assert set(file_ids) == {10, 20, 30, 40}
        scores = dict(fused)
        assert scores[20] == max(scores.values())

    def test_empty_bm25(self):
        from src.services.rag._summary_router import _rrf_fuse_file_lists

        vector = [(10, 0.9)]
        fused = _rrf_fuse_file_lists(vector, [], alpha=0.6, top_k=10)
        assert len(fused) == 1
        assert fused[0][0] == 10

    def test_empty_vector(self):
        from src.services.rag._summary_router import _rrf_fuse_file_lists

        bm25 = [{"file_id": 10, "meeting_id": 1, "score": 5.0}]
        fused = _rrf_fuse_file_lists([], bm25, alpha=0.6, top_k=10)
        assert len(fused) == 1
        assert fused[0][0] == 10

    def test_both_empty(self):
        from src.services.rag._summary_router import _rrf_fuse_file_lists

        fused = _rrf_fuse_file_lists([], [], alpha=0.6, top_k=10)
        assert fused == []

    def test_top_k_limit(self):
        from src.services.rag._summary_router import _rrf_fuse_file_lists

        vector = [(i, 0.9 - i * 0.1) for i in range(10)]
        bm25 = [{"file_id": i, "meeting_id": 1, "score": float(i)} for i in range(5, 15)]
        fused = _rrf_fuse_file_lists(vector, bm25, alpha=0.6, top_k=5)
        assert len(fused) <= 5
