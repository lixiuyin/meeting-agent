import ast
from pathlib import Path


def test_core_layer_does_not_import_service_layer() -> None:
    core_dir = Path(__file__).resolve().parents[2] / "src" / "core"
    violations: list[str] = []
    for path in core_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if (
                    module == "src.services"
                    or module.startswith("src.services.")
                    or (node.level and (module == "services" or module.startswith("services.")))
                ):
                    violations.append(f"{path.relative_to(core_dir)}:{node.lineno}: {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "src.services" or alias.name.startswith("src.services."):
                        violations.append(
                            f"{path.relative_to(core_dir)}:{node.lineno}: {alias.name}"
                        )

    assert not violations, "core -> services dependency inversion:\n" + "\n".join(violations)
