"""Fail closed on dependency findings outside the embedded-Chroma threat model."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

_MITIGATED_CHROMA_IDS = {
    "PYSEC-2026-311",
    "CVE-2026-45829",
    "CVE-2026-45830",
    "CVE-2026-45831",
    "CVE-2026-45833",
}
_FORBIDDEN_CHROMA_CALLS = {"HttpClient", "AsyncHttpClient", "CloudClient"}
_FORBIDDEN_IMPORT_PREFIXES = ("chromadb.server", "chromadb.api.fastapi")


def _source_policy_errors(source_root: Path) -> list[str]:
    errors: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                        errors.append(f"forbidden Chroma server import in {path}: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                    errors.append(f"forbidden Chroma server import in {path}: {module}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in _FORBIDDEN_CHROMA_CALLS:
                    errors.append(f"forbidden remote Chroma client in {path}: {node.func.attr}")
                for keyword in node.keywords:
                    if (
                        keyword.arg == "trust_remote_code"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        errors.append(f"trust_remote_code=True in {path}")
    return errors


def advisory_errors(payload: object, source_root: Path) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("dependencies"), list):
        return ["pip-audit JSON must contain a dependencies list"]

    errors = _source_policy_errors(source_root)
    for dependency in payload["dependencies"]:
        if not isinstance(dependency, dict):
            errors.append("invalid dependency entry in pip-audit JSON")
            continue
        name = str(dependency.get("name") or "")
        for advisory in dependency.get("vulns") or []:
            if not isinstance(advisory, dict):
                errors.append(f"invalid advisory entry for {name}")
                continue
            identifiers = {str(advisory.get("id") or ""), *map(str, advisory.get("aliases") or [])}
            fix_versions = advisory.get("fix_versions") or []
            if name != "chromadb" or not identifiers.intersection(_MITIGATED_CHROMA_IDS):
                errors.append(
                    f"unreviewed dependency advisory: {name} {advisory.get('id', 'unknown')}"
                )
            elif fix_versions:
                errors.append(
                    f"Chroma advisory {advisory.get('id')} now has a fix: {', '.join(fix_versions)}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("src"))
    args = parser.parse_args()
    try:
        payload = json.loads(args.audit.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read pip-audit JSON: {exc}")
        return 1
    errors = advisory_errors(payload, args.source_root)
    if errors:
        print("FAIL: dependency findings exceed the reviewed local-only Chroma policy:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "PASS: dependency findings are empty or limited to reviewed embedded-Chroma "
        "server advisories; remote/server clients and trust_remote_code remain prohibited"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
