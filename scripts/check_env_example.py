#!/usr/bin/env python3
"""Compatibility entry point for the canonical backend env drift check."""

from __future__ import annotations

import runpy
from pathlib import Path

_CHECKER = Path(__file__).resolve().parents[1] / "backend" / "scripts" / "check_env_example.py"
runpy.run_path(str(_CHECKER), run_name="__main__")
