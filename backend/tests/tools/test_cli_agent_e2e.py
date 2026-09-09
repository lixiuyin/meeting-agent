"""CLI end-to-end tests via subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.core import database as db
from src.core.config import settings


def _run_cli_scripted_input(
    scripted_input: str, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    backend_dir = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["DB_PATH"] = str(settings.DB_PATH)
    env["UPLOAD_DIR"] = str(settings.UPLOAD_DIR)
    env["VECTOR_DB_DIR"] = str(settings.VECTOR_DB_DIR)
    env["DATA_DIR"] = str(settings.DB_PATH.parent)
    return subprocess.run(
        [sys.executable, "-m", "scripts.cli_agent"],
        cwd=backend_dir,
        input=scripted_input,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def test_cli_e2e_help_and_quit() -> None:
    completed = _run_cli_scripted_input("/help\n/quit\n")

    assert completed.returncode == 0, completed.stderr
    assert "Available Commands" in completed.stdout
    assert "Goodbye!" in completed.stdout


def test_cli_e2e_settings_keys_and_get() -> None:
    completed = _run_cli_scripted_input("/settings keys rag\n/settings get rag.top_k\n/quit\n")

    assert completed.returncode == 0, completed.stderr
    assert "Settings Keys" in completed.stdout
    assert "Setting: rag.top_k" in completed.stdout


def test_cli_e2e_interactive_settings_set_flow() -> None:
    completed = _run_cli_scripted_input(
        "/settings set\nrag.top_k\n9\n/settings get rag.top_k\n/quit\n"
    )

    assert completed.returncode == 0, completed.stderr
    assert "Updated setting: rag.top_k = 9" in completed.stdout
    assert "Setting: rag.top_k" in completed.stdout


def test_cli_e2e_pagination_commands() -> None:
    completed = _run_cli_scripted_input(
        "/meetings --limit 1 --offset 0\n/sessions --limit 1 --offset 0\n/quit\n"
    )

    assert completed.returncode == 0, completed.stderr
    assert ("Uploaded Meetings" in completed.stdout) or ("No meetings found" in completed.stdout)
    assert ("Sessions (" in completed.stdout) or ("No chat sessions found" in completed.stdout)


def test_cli_e2e_export_command_handles_missing_meeting(tmp_path: Path) -> None:
    output_file = tmp_path / "e2e-export.json"

    completed = _run_cli_scripted_input(
        f"/export 999999 --format json --output {output_file}\n/quit\n"
    )

    assert completed.returncode == 0, completed.stderr
    assert "Export failed: 404: Meeting not found" in completed.stdout
    assert not output_file.exists()


def test_cli_e2e_export_command_writes_file_for_ready_meeting(tmp_path: Path) -> None:
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="CLI E2E Export", user_id="test")
        db.update_meeting_status(conn, meeting_id, "processing")
        db.update_meeting_status(conn, meeting_id, "ready", transcript="export transcript from e2e")

    output_file = tmp_path / "e2e-export-success.json"

    completed = _run_cli_scripted_input(
        f"/export {meeting_id} --format json --output {output_file}\n/quit\n"
    )

    assert completed.returncode == 0, completed.stderr
    assert "Exported meeting" in completed.stdout
    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "export transcript from e2e" in content
