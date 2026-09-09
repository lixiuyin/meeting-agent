"""Restrict local runtime data so another host account cannot read it."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)


def sanitize_legacy_pipeline_logs(data_root: Path) -> int:
    """Remove raw questions left by older pipeline log writers.

    Invalid legacy lines are replaced by a digest-only marker so an unexpected
    partial write cannot preserve user content indefinitely.
    """
    sanitized = 0
    log_dir = data_root / "logs"
    if not log_dir.is_dir() or log_dir.is_symlink():
        return sanitized
    for path in sorted(log_dir.glob("pipeline.jsonl*")):
        if path.is_symlink() or not path.is_file():
            continue
        changed = False
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=log_dir,
            prefix=f".{path.name}.",
            suffix=".sanitize",
            delete=False,
        ) as destination:
            temporary = Path(destination.name)
            os.fchmod(destination.fileno(), 0o600)
            with path.open(encoding="utf-8", errors="replace") as source:
                for line in source:
                    raw = line.rstrip("\n")
                    document: object
                    try:
                        document = json.loads(raw)
                    except json.JSONDecodeError:
                        document = {
                            "status": "redacted_malformed_legacy_log_line",
                            "line_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                        }
                        changed = True
                    if isinstance(document, dict) and "question" in document:
                        record = cast(dict[str, object], document)
                        question = str(record.pop("question"))
                        record.setdefault(
                            "question_sha256", hashlib.sha256(question.encode()).hexdigest()
                        )
                        record.setdefault("question_chars", len(question))
                        changed = True
                    destination.write(json.dumps(document, ensure_ascii=False) + "\n")
        if changed:
            os.replace(temporary, path)
            path.chmod(0o600)
            sanitized += 1
        else:
            temporary.unlink()
    return sanitized


def harden_private_path(root: Path) -> list[str]:
    """Set directories to 0700 and regular files to 0600 without following links."""
    errors: list[str] = []
    if not root.exists() or root.is_symlink():
        return errors
    paths = [root, *root.rglob("*")]
    for path in paths:
        try:
            if path.is_symlink():
                continue
            if path.is_dir():
                path.chmod(0o700)
            elif path.is_file():
                path.chmod(0o600)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return errors


def harden_runtime_permissions(*roots: Path) -> None:
    """Apply a private umask and normalize existing configured data trees."""
    os.umask(0o077)
    sanitized = sum(sanitize_legacy_pipeline_logs(root) for root in roots)
    errors = [error for root in roots for error in harden_private_path(root)]
    if errors:
        detail = "; ".join(errors[:5])
        if len(errors) > 5:
            detail += f"; and {len(errors) - 5} more"
        raise PermissionError(f"Could not restrict runtime data permissions: {detail}")
    logger.info(
        "Restricted runtime data permissions for %d root(s); sanitized %d legacy log file(s)",
        len(roots),
        sanitized,
    )
