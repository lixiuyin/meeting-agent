"""Canonical upload-path resolution shared by ingestion and media APIs."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ...core.config import settings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_upload_path(stored_path: str | Path, *, expected_hash: str | None = None) -> Path:
    """Resolve a stored upload path and reject ambiguous basename rebases.

    Absolute paths can legitimately change between Docker and local execution.
    A basename rebase is accepted only when its bytes match the persisted hash.
    """
    root = settings.UPLOAD_DIR.resolve()
    stored = Path(stored_path)
    rebased = not stored.exists()
    candidate = settings.UPLOAD_DIR / stored.name if rebased else stored
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"Uploaded file not found: {stored}")
    if candidate.is_symlink():
        raise ValueError(f"Uploaded file path is a symlink: {candidate}")
    resolved = candidate.resolve()
    if rebased and not resolved.is_relative_to(root):
        raise ValueError(f"Uploaded file is outside the upload root: {resolved}")
    if rebased and not expected_hash:
        raise ValueError(f"Cannot safely rebase upload without a content hash: {stored}")
    if expected_hash and _sha256(resolved) != expected_hash:
        raise ValueError(f"Rebased upload content hash does not match database record: {stored}")
    return resolved
