"""CJK trigram index for file-summary lexical routing."""

import sqlite3

FILE_SUMMARY_CJK_SCHEMA_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS file_summary_fts_cjk USING fts5(
    summary,
    content='file_summary_bm25',
    content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS file_summary_fts_cjk_ai
AFTER INSERT ON file_summary_bm25 BEGIN
    INSERT INTO file_summary_fts_cjk(rowid, summary) VALUES (new.id, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS file_summary_fts_cjk_ad
AFTER DELETE ON file_summary_bm25 BEGIN
    INSERT INTO file_summary_fts_cjk(file_summary_fts_cjk, rowid, summary)
    VALUES('delete', old.id, old.summary);
END;
CREATE TRIGGER IF NOT EXISTS file_summary_fts_cjk_au
AFTER UPDATE ON file_summary_bm25 BEGIN
    INSERT INTO file_summary_fts_cjk(file_summary_fts_cjk, rowid, summary)
    VALUES('delete', old.id, old.summary);
    INSERT INTO file_summary_fts_cjk(rowid, summary) VALUES (new.id, new.summary);
END;
"""


def ensure_file_summary_cjk_schema(conn: sqlite3.Connection) -> None:
    existed = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_summary_fts_cjk'"
    ).fetchone()
    conn.executescript(FILE_SUMMARY_CJK_SCHEMA_SQL)
    if not existed:
        conn.execute("INSERT INTO file_summary_fts_cjk(file_summary_fts_cjk) VALUES('rebuild')")
