#!/usr/bin/env python3
"""Validate that .env.example defaults are consistent with config.py Settings.

Finds every field in ``src.core.config.Settings`` that has a corresponding
entry in ``.env.example`` and asserts the example value matches the pydantic
default.  Exits non-zero on any mismatch.

Usage:  uv run python scripts/check_env_example.py
"""

import os
import re
import sys

_ENV_EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "backend", ".env.example")


def parse_env_example(path: str) -> dict[str, str]:
    """Parse KEY=VALUE pairs from .env.example, skipping comments and blanks."""
    result: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            if value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            result[key] = value
    return result


def main() -> int:
    if not os.path.exists(_ENV_EXAMPLE):
        print(f"ERROR: .env.example not found at {_ENV_EXAMPLE}")
        return 1

    env_example = parse_env_example(_ENV_EXAMPLE)

    # Import settings and inspect field defaults
    from src.core.config import Settings

    # Build map of env_name → (default, annotation) from pydantic fields
    mismatches: list[str] = []
    for name, field in Settings.model_fields.items():
        # Fields without a default are required (env-only, not in .env.example)
        if field.default is None and field.default_factory is None:
            continue
        # Skip fields that shouldn't be in .env.example
        if name.startswith("_") or name in (
            "model_config",
            "API_KEY",
            "LLM_API_KEY",
            "ASSEMBLYAI_API_KEY",
            "EMBEDDING_API_KEY",
            "SEARCH_API_KEY",
            "RERANKER_API_KEY",
            "VISION_API_KEY",
            "TTS_API_KEY",
            "MARKER_API_KEY",
            "MINERU_API_KEY",
            "PADDLEOCR_API_KEY",
        ):
            continue

        if name not in env_example:
            continue  # Field not in .env.example, acceptable

        default = field.default
        if default is None:
            continue

        env_val = env_example[name]

        # Normalize for comparison
        expected = str(default)
        if expected != env_val:
            mismatches.append(
                f"  {name}: .env.example={env_val!r}  config.py default={expected!r}"
            )

    if mismatches:
        print(f"ERROR: {len(mismatches)} config-drift issue(s) found:\n")
        for m in mismatches:
            print(m)
        print("\nFix: align .env.example values with config.py defaults.")
        return 1

    print(f"OK: .env.example is consistent with config.py defaults ({len(env_example)} keys checked)")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))
    os.chdir(os.path.join(os.path.dirname(__file__), "..", "backend"))
    sys.exit(main())
