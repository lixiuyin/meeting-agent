"""Launch validation with fresh paths before importing any application code.

macOS additionally denies writes to configured application assets with Seatbelt.
On other hosts use a disposable CI/container without writable application mounts;
environment isolation alone is not an OS security boundary.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    command = sys.argv[1:]
    load_providers = command[:1] == ["--provider-config"]
    if load_providers:
        command = command[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("Usage: run-isolated.py -- command [arguments]")
    if sys.platform == "darwin" and not os.environ.get("MEETING_AGENT_PROTECTED_RUN"):
        # Resolve protected paths using the ORIGINAL environment, before overrides.
        return subprocess.call(
            [
                sys.executable,
                str(Path(__file__).with_name("run-protected.py")),
                "--",
                sys.executable,
                str(Path(__file__).resolve()),
                *(["--provider-config"] if load_providers else []),
                "--",
                *command,
            ]
        )
    with tempfile.TemporaryDirectory(prefix="meeting-agent-validation-") as directory:
        root = Path(directory)
        provider_env = {}
        if load_providers:
            from dotenv import dotenv_values

            values = dotenv_values(Path(__file__).resolve().parents[1] / "backend/.env")
            prefixes = ("LLM_", "EMBEDDING_", "RERANKER_", "MARKER_", "MINERU_", "ASR_")
            provider_env = {
                key: value
                for key, value in values.items()
                if key.startswith(prefixes) and value is not None
            }
        env = {
            **provider_env,
            **os.environ,
            "MEETING_AGENT_DISABLE_DOTENV": "1",
            "DATA_DIR": str(root),
            "DB_PATH": str(root / "meetings.db"),
            "UPLOAD_DIR": str(root / "uploads"),
            "VECTOR_DB_DIR": str(root / "vectors"),
            "LOG_DIR": str(root / "logs"),
            "CUSTOM_SKILLS_DIR": str(root / "skills"),
            "ENVIRONMENT": "dev",
            "API_KEY": "",
        }
        return subprocess.call(command, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
