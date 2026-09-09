"""Run local validation with OS-enforced write denial for application data.

On macOS, use Seatbelt rather than relying on application configuration alone.
Unsupported hosts fail closed; run their validation in an isolated container.
This launcher never imports src or starts the application in its own process.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def protected_paths(root: Path, extra: list[str]) -> set[Path]:
    paths = {root / "data", root / "backups"}
    # Import dotenv only, never application settings (which may initialize state).
    from dotenv import dotenv_values

    # Protect both configured application locations and environment overrides:
    # a test DATA_DIR override must not remove protection from the .env location.
    for values in (
        dotenv_values(root / ".env"),
        dotenv_values(root / "backend" / ".env"),
        os.environ,
    ):
        for key in (
            "DATA_DIR",
            "DB_PATH",
            "UPLOAD_DIR",
            "VECTOR_DB_DIR",
            "LOG_DIR",
            "CUSTOM_SKILLS_DIR",
        ):
            value = values.get(key)
            if value:
                path = Path(value).expanduser()
                # Relative configuration can be interpreted from either launch dir.
                paths.update(
                    [path]
                    if path.is_absolute()
                    else [root / path, root / "backend" / path]
                )
    paths.update(Path(value).expanduser().absolute() for value in extra)
    return paths | {path.resolve() for path in paths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protect", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a command after --")
    sandbox = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
    if not sandbox:
        parser.error(
            "OS isolation unavailable; use a disposable container with no writable data mount"
        )
    root = Path(__file__).resolve().parents[1]
    paths = protected_paths(root, args.protect)
    rules = "\n".join(f"(subpath {json.dumps(str(path))})" for path in sorted(paths))
    profile = f"(version 1) (allow default) (deny file-write* {rules})"
    return subprocess.call(
        [sandbox, "-p", profile, *command],
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "MEETING_AGENT_PROTECTED_RUN": "1",
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
