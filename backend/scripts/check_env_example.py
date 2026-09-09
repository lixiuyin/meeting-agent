#!/usr/bin/env python3
"""CI check that the compact ``.env.example`` matches its generator."""

from __future__ import annotations

import difflib
import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_EXAMPLE = _BACKEND_DIR / ".env.example"
_PRIVATE_ENV = _BACKEND_DIR / ".env"


def _layout(text: str) -> list[str]:
    """Return comments/order/keys while removing private assignment values."""
    layout: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, _ = line.split("=", 1)
            layout.append(f"{key.strip()}=")
        else:
            layout.append(line)
    return layout


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

    committed = _ENV_EXAMPLE.read_text()
    failed = False
    if committed != result.stdout:
        failed = True
        print("FAIL: .env.example differs from the generated compact template:")
        print(
            "".join(
                difflib.unified_diff(
                    committed.splitlines(keepends=True),
                    result.stdout.splitlines(keepends=True),
                    fromfile="backend/.env.example",
                    tofile="generated",
                )
            )
        )
        print("Run from backend/: python -m scripts.gen_env_example > .env.example")

    if _PRIVATE_ENV.exists():
        private_text = _PRIVATE_ENV.read_text()
        if _layout(private_text) != _layout(committed):
            failed = True
            print("FAIL: .env and .env.example do not share the canonical layout.")
            print("Run: python -m scripts.gen_env_example --sync .env")
        if _PRIVATE_ENV.stat().st_mode & 0o077:
            failed = True
            print("FAIL: .env must not be readable or writable by group/others (use mode 600).")

    if failed:
        return 1
    print("PASS: .env and .env.example share the canonical compact layout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
