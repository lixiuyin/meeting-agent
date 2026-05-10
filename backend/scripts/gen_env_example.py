#!/usr/bin/env python3
"""Generate .env.example from pydantic-settings schema.

Outputs a template with all user-configurable settings, their defaults,
and descriptions. Internal/computed fields (BASE_DIR, UPLOAD_DIR, etc.)
are excluded.

Usage:
    python -m scripts.gen_env_example > /tmp/env_example
    diff /tmp/env_example backend/.env.example
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend/ is on sys.path so `src` resolves
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

# Must set env vars before importing config to avoid validation errors
import os

os.environ.setdefault("LLM_API_KEY", "placeholder")
os.environ.setdefault("EMBEDDING_API_KEY", "placeholder")
os.environ.setdefault("ASSEMBLYAI_API_KEY", "placeholder")

from src.core.config import Settings  # noqa: E402

# Fields that are internal/computed — should not appear in .env.example
_SKIP_FIELDS = frozenset({
    "BASE_DIR",
    "UPLOAD_DIR",
    "VECTOR_DB_DIR",
    "DB_PATH",
    "IDEMPOTENCY_OLD_KEYS",
})


def _format_default(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def main() -> None:
    model = Settings.model_fields
    for name in sorted(model):
        if name in _SKIP_FIELDS:
            continue
        field = model[name]
        default = _format_default(field.default)
        alias = field.alias or name
        print(f"{alias}={default}")


if __name__ == "__main__":
    main()
