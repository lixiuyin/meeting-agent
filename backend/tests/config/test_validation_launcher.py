"""Validation must protect all configuration sources without importing the app."""

import importlib.util
from pathlib import Path


def test_protects_backend_dotenv_and_overridden_paths(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[3] / "scripts/run-protected.py"
    spec = importlib.util.spec_from_file_location("validation_guard", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "repo"
    external = tmp_path / "external"
    skills = tmp_path / "skills"
    sources = {
        root / ".env": {"UPLOAD_DIR": "old-uploads"},
        root / "backend/.env": {"DATA_DIR": str(external), "CUSTOM_SKILLS_DIR": str(skills)},
    }
    monkeypatch.setattr("dotenv.dotenv_values", lambda path: sources[path])
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "override"))
    paths = module.protected_paths(root, [])
    assert {
        external,
        skills,
        root / "old-uploads",
        root / "backend/old-uploads",
        tmp_path / "override",
    } <= paths
