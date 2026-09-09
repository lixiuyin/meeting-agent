#!/usr/bin/env python3
"""Validate local links, Mermaid fences, and English/Chinese README parity."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DOC_ROOTS = (
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "CHANGELOG.md",
    ROOT / "CODE_OF_CONDUCT.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "docs",
    ROOT / "backend" / "docs",
    ROOT / "backend" / "evaluation",
    ROOT / "backend" / "skills",
    ROOT
    / "backend"
    / "src"
    / "services"
    / "processor"
    / "_processors"
    / "non-text-chunking-toggle.md",
    ROOT / "backend" / "tests" / "fixtures" / "benchmark" / "README.md",
    ROOT / "frontend" / "docs",
    ROOT / "deploy" / "helm" / "meeting-agent" / "README.md",
)
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
METHOD_PATH_ROW_RE = re.compile(
    r"^\|\s*`(GET|POST|PUT|PATCH|DELETE)`\s*\|\s*`([^`]+)`", re.MULTILINE
)
ENV_KEY_RE = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")
HEADING_RE = re.compile(r"^(#{1,6}) ", re.MULTILINE)
SHELL_FENCE_RE = re.compile(r"```(?:bash|sh)\n(.*?)```", re.DOTALL)
MERMAID_STARTS = {
    "architecture-beta",
    "block-beta",
    "classDiagram",
    "erDiagram",
    "flowchart",
    "gantt",
    "gitGraph",
    "graph",
    "journey",
    "mindmap",
    "pie",
    "quadrantChart",
    "requirementDiagram",
    "sequenceDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "timeline",
    "xychart-beta",
}


def markdown_files() -> list[Path]:
    files: set[Path] = set()
    for target in DOC_ROOTS:
        if target.is_file():
            files.add(target)
        elif target.is_dir():
            files.update(target.rglob("*.md"))
    return sorted(files)


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    in_mermaid = False
    mermaid_lines: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped == "```mermaid":
            if in_mermaid:
                errors.append(f"{path.relative_to(ROOT)}:{line_number}: nested Mermaid fence")
            in_mermaid = True
            mermaid_lines = []
            continue
        if in_mermaid and stripped == "```":
            content = [
                item.strip()
                for item in mermaid_lines
                if item.strip() and not item.lstrip().startswith("%%")
            ]
            if not content or content[0].split(maxsplit=1)[0] not in MERMAID_STARTS:
                errors.append(
                    f"{path.relative_to(ROOT)}:{line_number}: invalid Mermaid diagram header"
                )
            in_mermaid = False
            continue
        if in_mermaid:
            mermaid_lines.append(line)

    if in_mermaid:
        errors.append(f"{path.relative_to(ROOT)}: unclosed Mermaid fence")

    for match in LINK_RE.finditer(text):
        raw_target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
        if not raw_target or raw_target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = unquote(raw_target.split("#", 1)[0].split("?", 1)[0])
        if not target:
            continue
        if not (path.parent / target).resolve().exists():
            line_number = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{path.relative_to(ROOT)}:{line_number}: missing local link target {raw_target}"
            )
    return errors


def _local_link_targets(text: str) -> set[str]:
    targets: set[str] = set()
    for match in LINK_RE.finditer(text):
        raw_target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
        if not raw_target or raw_target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = unquote(raw_target.split("#", 1)[0].split("?", 1)[0])
        if target and target not in {"README.md", "README.zh-CN.md"}:
            targets.add(target)
    return targets


def _shell_commands(text: str) -> set[str]:
    commands: set[str] = set()
    for block in SHELL_FENCE_RE.findall(text):
        for line in block.splitlines():
            command = line.strip()
            if not command or command.startswith("#"):
                continue
            commands.add(re.split(r"\s+#\s", command, maxsplit=1)[0].rstrip())
    return commands


def validate_readme_parity() -> list[str]:
    """Keep machine-verifiable English/Chinese README contracts aligned."""

    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    checks = {
        "heading levels": (
            [len(match.group(1)) for match in HEADING_RE.finditer(english)],
            [len(match.group(1)) for match in HEADING_RE.finditer(chinese)],
        ),
        "documented API method/path rows": (
            set(METHOD_PATH_ROW_RE.findall(english)),
            set(METHOD_PATH_ROW_RE.findall(chinese)),
        ),
        "environment/configuration keys": (
            set(ENV_KEY_RE.findall(english)),
            set(ENV_KEY_RE.findall(chinese)),
        ),
        "local documentation link targets": (
            _local_link_targets(english),
            _local_link_targets(chinese),
        ),
        "shell commands": (_shell_commands(english), _shell_commands(chinese)),
    }
    errors: list[str] = []
    for label, (english_value, chinese_value) in checks.items():
        if english_value != chinese_value:
            errors.append(
                "README.md / README.zh-CN.md mismatch in "
                f"{label}: English-only={sorted(set(english_value) - set(chinese_value))}; "
                f"Chinese-only={sorted(set(chinese_value) - set(english_value))}"
            )
    return errors


def main() -> int:
    files = markdown_files()
    errors = [error for path in files for error in validate_file(path)]
    errors.extend(validate_readme_parity())
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Documentation checks passed ({len(files)} Markdown files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
