#!/usr/bin/env python3
"""Generate or apply the compact Meeting Agent environment template.

``config/main.yaml`` owns non-secret defaults and advanced tuning. Both
``.env`` and ``.env.example`` use the same short layout; only their values
differ.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr

_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

# Do not let a local .env alter the defaults used to build the template.
os.environ["MEETING_AGENT_DISABLE_DOTENV"] = "1"

from src.core.config import Settings  # noqa: E402

_SECRET_FIELDS = frozenset(
    {
        "LLM_API_KEY",
        "VISION_API_KEY",
        "EMBEDDING_API_KEY",
        "ASSEMBLYAI_API_KEY",
        "MARKER_API_KEY",
        "MINERU_API_KEY",
        "PADDLEOCR_API_KEY",
        "SEARCH_API_KEY",
        "RERANKER_API_KEY",
        "API_KEY",
        "PRINCIPAL_PEPPER",
    }
)

_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "# Core LLM. LLM_API_KEY may be empty only for a local provider.",
        ("LLM_BINDING", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"),
    ),
    (
        "# Optional role-specific models. Empty extraction model reuses LLM_MODEL.",
        (
            "MEMORY_EXTRACTION_MODEL",
            "VISION_MODEL",
            "VISION_API_KEY",
            "VISION_BASE_URL",
            "VISION_REASONING_EFFORT",
            "VISION_COMBINED_MAX_TOKENS",
        ),
    ),
    (
        "# Embeddings. Leave EMBEDDING_API_KEY empty to reuse LLM_API_KEY when supported.",
        (
            "EMBEDDING_BINDING",
            "EMBEDDING_MODEL",
            "EMBEDDING_DIMENSION",
            "EMBEDDING_API_KEY",
            "EMBEDDING_BASE_URL",
        ),
    ),
    (
        "# Optional audio transcription.",
        ("ASSEMBLYAI_API_KEY",),
    ),
    (
        "# Optional document parsers. OCR_PROVIDER selects marker, mineru, or paddleocr.",
        (
            "OCR_PROVIDER",
            "MARKER_BASE_URL",
            "MARKER_API_KEY",
            "MINERU_BASE_URL",
            "MINERU_API_KEY",
            "PADDLEOCR_BASE_URL",
            "PADDLEOCR_API_KEY",
        ),
    ),
    (
        "# Optional web search.",
        ("SEARCH_BINDING", "SEARCH_API_KEY"),
    ),
    (
        (
            "# Optional reranking. Leave RERANKER_BINDING empty to disable it.\n"
            "# For OpenRouter or another Cohere-compatible API, set "
            "RERANKER_BINDING=cohere.\n"
            "# API key: set RERANKER_API_KEY for a separate key, or leave it empty to reuse "
            "LLM_API_KEY.\n"
            "# The key that is used must be valid for RERANKER_BASE_URL."
        ),
        ("RERANKER_BINDING", "RERANKER_MODEL", "RERANKER_API_KEY", "RERANKER_BASE_URL"),
    ),
    (
        (
            "# Deployment security. Set independent secrets for non-development environments.\n"
            "# PRINCIPAL_ID optionally pins a stable existing principal across API-key rotation;\n"
            "# for existing data, copy the owned ID from the database instead of inventing one."
        ),
        ("ENVIRONMENT", "API_KEY", "PRINCIPAL_PEPPER", "PRINCIPAL_ID"),
    ),
)


def _default(name: str) -> str:
    if name in _SECRET_FIELDS:
        return ""
    value = Settings.model_fields[name].default
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def render(values: Mapping[str, str] | None = None) -> str:
    """Render the canonical layout with defaults or supplied private values."""
    supplied = values or {}
    lines = [
        "# Meeting Agent runtime configuration.",
        "# Advanced RAG, memory, limits, and timeout defaults live in config/main.yaml.",
    ]
    for comment, names in _SECTIONS:
        lines.extend(("", comment))
        for name in names:
            lines.append(f"{name}={supplied.get(name, _default(name))}")
    return "\n".join(lines) + "\n"


def _read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _sync_private_env(path: Path) -> None:
    """Atomically normalize a private env file without printing its values."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render(_read_values(path))
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Synced {path.name} to the canonical layout; values were not displayed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sync",
        type=Path,
        metavar="ENV_FILE",
        help="normalize an existing private env file while preserving known values",
    )
    args = parser.parse_args()
    if args.sync:
        _sync_private_env(args.sync)
    else:
        print(render(), end="")


if __name__ == "__main__":
    main()
