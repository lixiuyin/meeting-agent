"""Acquire one stable source copy; all evaluation cases clone this same baseline.

Requires quiescent source writers. Before/after identity checks reject detected
changes; they are not a cross-storage transaction or a distributed snapshot.
"""

import contextlib
import hashlib
import shutil
import sqlite3
import tempfile
from pathlib import Path

from ._holdout_identity import tree_digest


def corpus_identity(database: Path, vectors: Path) -> tuple[str, str]:
    digest = hashlib.sha256()
    with contextlib.closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    ) as conn:
        conn.execute("BEGIN")
        for statement in conn.iterdump():
            digest.update(statement.encode())
    return digest.hexdigest(), tree_digest(vectors)


@contextlib.contextmanager
def frozen_corpus(database: Path, vectors: Path):
    if not database.is_file() or not vectors.is_dir():
        raise ValueError("A complete database and vector corpus is required")
    before = corpus_identity(database, vectors)
    with tempfile.TemporaryDirectory(prefix="meeting-eval-baseline-") as directory:
        root = Path(directory)
        db_copy, vector_copy = root / "baseline.db", root / "vectors"
        with (
            contextlib.closing(
                sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            ) as source,
            contextlib.closing(sqlite3.connect(db_copy)) as target,
        ):
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Corpus database integrity check failed")
        shutil.copytree(vectors, vector_copy)
        if (
            corpus_identity(database, vectors) != before
            or corpus_identity(db_copy, vector_copy) != before
        ):
            raise ValueError("Source changed while copying; stop corpus writers and retry")
        yield db_copy, vector_copy
