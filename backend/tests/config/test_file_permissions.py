from __future__ import annotations

import json
import stat

from src.core.file_permissions import harden_runtime_permissions


def test_hardening_sanitizes_legacy_questions_and_permissions(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    pipeline_log = log_dir / "pipeline.jsonl"
    pipeline_log.write_text(
        json.dumps({"trace_id": "one", "question": "private meeting question"})
        + "\n"
        + "partial private text\n",
        encoding="utf-8",
    )
    pipeline_log.chmod(0o644)
    log_dir.chmod(0o755)

    harden_runtime_permissions(tmp_path)

    rows = [json.loads(line) for line in pipeline_log.read_text().splitlines()]
    assert "question" not in rows[0]
    assert rows[0]["question_chars"] == len("private meeting question")
    assert len(rows[0]["question_sha256"]) == 64
    assert rows[1]["status"] == "redacted_malformed_legacy_log_line"
    assert "partial private text" not in pipeline_log.read_text()
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(pipeline_log.stat().st_mode) == 0o600
