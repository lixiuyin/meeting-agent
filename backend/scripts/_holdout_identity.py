"""Content identities, never credentials, for reproducible paid evaluations."""

import contextlib
import hashlib
import json
import sqlite3
from pathlib import Path


def tree_digest(root: Path, *, suffixes: set[str] | None = None) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "missing"
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
            continue
        if suffixes is not None and path.suffix not in suffixes:
            continue
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def run_identity(args, settings, backend_dir: Path, holdout_hash: str) -> str:
    config = settings.model_dump(mode="json")
    config = {
        key: value
        for key, value in config.items()
        if not any(secret in key.upper() for secret in ("API_KEY", "SECRET", "PASSWORD"))
    }
    db_hash = hashlib.sha256()
    with contextlib.closing(
        sqlite3.connect(args.source_db.resolve().as_uri() + "?mode=ro", uri=True)
    ) as conn:
        conn.execute("BEGIN")
        for statement in conn.iterdump():
            db_hash.update(statement.encode())
    payload = {
        "protocol": 3,
        "holdout": holdout_hash,
        "config": config,
        "arguments": {
            key: str(value)
            for key, value in vars(args).items()
            if key not in {"output", "func", "source_db", "source_vector_dir"}
        },
        "database": db_hash.hexdigest(),
        "vectors": tree_digest(args.source_vector_dir.resolve()),
        "code": [
            tree_digest(backend_dir / folder, suffixes={".py", ".md", ".json", ".yaml", ".txt"})
            for folder in ("src", "scripts")
        ],
        "dependencies": {
            name: hashlib.sha256((backend_dir / name).read_bytes()).hexdigest()
            for name in ("pyproject.toml", "uv.lock")
            if (backend_dir / name).exists()
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
