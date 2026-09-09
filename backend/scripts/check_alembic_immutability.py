"""Fail when an already-published Alembic revision was edited or removed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def main() -> int:
    backend_dir = Path(__file__).resolve().parent.parent
    manifest_path = backend_dir / "alembic" / "immutable_revisions.json"
    revisions_dir = backend_dir / "alembic" / "versions"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    for filename, expected in sorted(manifest.items()):
        path = revisions_dir / filename
        if not path.is_file():
            errors.append(f"immutable revision removed: {filename}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(
                f"immutable revision changed: {filename} (expected {expected}, got {actual})"
            )

    if errors:
        print("\n".join(errors))
        print("Create a new forward Alembic revision; never edit a published revision.")
        return 1
    print(f"Verified {len(manifest)} immutable Alembic revisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
