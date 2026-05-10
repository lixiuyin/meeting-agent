#!/usr/bin/env python3
"""CI check: verify .env.example is in sync with pydantic-settings schema.

Exit 1 with a diff if the committed .env.example is stale.
"""

from __future__ import annotations

import difflib
import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_EXAMPLE = _BACKEND_DIR / ".env.example"


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.gen_env_example"],
        capture_output=True,
        text=True,
        cwd=str(_BACKEND_DIR),
    )
    if result.returncode != 0:
        print("ERROR: gen_env_example failed", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return 1

    generated = result.stdout
    committed = _ENV_EXAMPLE.read_text()

    # Compare only non-blank, non-comment lines for robustness
    gen_lines = [l for l in generated.splitlines() if l.strip() and not l.startswith("#")]
    com_lines = [l for l in committed.splitlines() if l.strip() and not l.startswith("#")]

    # Extract variable names (before =) from both
    gen_vars = {l.split("=", 1)[0] for l in gen_lines if "=" in l}
    com_vars = {l.split("=", 1)[0] for l in com_lines if "=" in l}

    missing_in_example = gen_vars - com_vars
    extra_in_example = com_vars - gen_vars

    if missing_in_example:
        print("FAIL: .env.example is missing variables present in Settings:")
        for v in sorted(missing_in_example):
            print(f"  - {v}")
        print("\nRun: python -m scripts.gen_env_example > backend/.env.example")

    if extra_in_example:
        print("FAIL: .env.example has variables not in Settings (stale?):")
        for v in sorted(extra_in_example):
            print(f"  + {v}")

    if missing_in_example or extra_in_example:
        return 1

    print("PASS: .env.example is in sync with Settings schema")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
