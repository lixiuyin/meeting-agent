import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("phase", ["after_swap", "after_manifest", "after_commit"])
def test_process_crash_recovers_matching_native_stores(tmp_path, phase):
    backend = Path(__file__).resolve().parents[2]
    harness = backend / "tests/fixtures/rebuild_crash_probe.py"
    env = {
        **os.environ,
        "MEETING_AGENT_DISABLE_DOTENV": "1",
        "DATA_DIR": str(tmp_path),
        "DB_PATH": str(tmp_path / "meetings.db"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "VECTOR_DB_DIR": str(tmp_path / "vectors"),
        "LOG_DIR": str(tmp_path / "logs"),
        "CUSTOM_SKILLS_DIR": str(tmp_path / "skills"),
        "PYTHONPATH": str(backend),
    }
    interrupted = subprocess.run(
        [sys.executable, str(harness), phase],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert interrupted.returncode == 97, interrupted.stderr
    recovered = subprocess.run(
        [sys.executable, str(harness), "recover"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout.strip().splitlines()[-1])
    expected = "new" if phase == "after_commit" else "old"
    assert result == {"vectors": [expected], "bm25": [expected], "state": ["ready", expected, 0]}
