"""Isolation guarantees for the benchmark environment."""

import os
from pathlib import Path

import pytest

from scripts._bench_env import bench_environment
from scripts._bench_fixtures import _ensure_meeting


def test_bench_environment_restores_runtime_paths() -> None:
    import src.core.constants as constants_module
    from src.core.config import settings

    env_before = {
        key: os.environ.get(key) for key in ("DATA_DIR", "DB_PATH", "UPLOAD_DIR", "VECTOR_DB_DIR")
    }
    constants_before = {
        key: getattr(constants_module, key)
        for key in ("DATA_DIR", "DB_PATH", "UPLOAD_DIR", "VECTOR_DB_DIR")
    }
    settings_before = {
        key: getattr(settings, key) for key in ("DB_PATH", "UPLOAD_DIR", "VECTOR_DB_DIR")
    }

    with bench_environment() as temporary_root:
        assert temporary_root / "data" == constants_module.DATA_DIR
        assert temporary_root / "data" / "meetings.db" == settings.DB_PATH
        assert Path(os.environ["DB_PATH"]) == settings.DB_PATH

    assert not temporary_root.exists()
    assert {key: os.environ.get(key) for key in env_before} == env_before
    assert {key: getattr(constants_module, key) for key in constants_before} == constants_before
    assert {key: getattr(settings, key) for key in settings_before} == settings_before


def test_benchmark_meeting_has_explicit_tenant(monkeypatch) -> None:
    captured = {}

    def _fake_create_meeting(_conn, **kwargs):
        captured.update(kwargs)
        return 42

    monkeypatch.setattr("scripts._bench_fixtures.create_meeting", _fake_create_meeting)

    assert _ensure_meeting(object(), "Fixture", "2026-01-15") == 42
    assert captured["user_id"] == "benchmark"


def test_chroma_cleanup_stops_only_owned_directory(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from chromadb.api.shared_system_client import SharedSystemClient

    from scripts._bench_env import release_isolated_chroma

    owned, foreign = Mock(), Mock()
    root = tmp_path / "owned"
    cache = {str(root / "vectors"): owned, str(tmp_path / "foreign"): foreign}
    monkeypatch.setattr(SharedSystemClient, "_identifier_to_system", cache)
    monkeypatch.setattr(SharedSystemClient, "_identifier_to_refcount", dict.fromkeys(cache, 1))
    release_isolated_chroma(root)
    owned.stop.assert_called_once()
    foreign.stop.assert_not_called()
    assert list(cache) == [str(tmp_path / "foreign")]


@pytest.mark.parametrize(
    "command", ["chat", "ingest", "multi-turn", "rag-retrieval", "rag-answer", "rag-snapshot"]
)
def test_cli_initializes_temporary_paths_before_runtime_import(command, tmp_path):
    """A fresh process catches imports hidden by pytest's preconfigured settings."""
    import subprocess
    import sys

    script = r"""
import builtins, os, sys
from pathlib import Path
from scripts import benchmark
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.startswith("src."):
        assert Path(os.environ.get("DATA_DIR", "/")).parent.name.startswith("benchmark_"), name
        raise SystemExit(0)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
args = benchmark._build_parser().parse_args([sys.argv[1]])
getattr(benchmark, "run_" + sys.argv[1].replace("-", "_") + "_benchmark")(args)
raise AssertionError("runtime was never entered")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, command],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
