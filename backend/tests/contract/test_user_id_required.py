"""Contract test: all create_meeting calls must pass user_id explicitly."""

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent.parent / "src"


def _find_create_meeting_calls() -> list[tuple[str, int, str]]:
    """Find all db.create_meeting(...) call sites in backend/src/."""
    violations: list[tuple[str, int, str]] = []
    for py_file in SRC.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match db.create_meeting(...) or create_meeting(...)
            func = node.func
            is_attr = isinstance(func, ast.Attribute) and func.attr == "create_meeting"
            is_name = isinstance(func, ast.Name) and func.id == "create_meeting"
            if not (is_attr or is_name):
                continue
            # Check if user_id is in kwargs
            kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
            if "user_id" not in kwargs:
                violations.append(
                    (str(py_file.relative_to(SRC.parent)), node.lineno, ast.unparse(node)[:120])
                )
    return violations


def test_all_create_meeting_calls_pass_user_id():
    violations = _find_create_meeting_calls()
    assert not violations, (
        f"Found {len(violations)} create_meeting call(s) without user_id:\n"
        + "\n".join(f"  {f}:{line}: {snippet}" for f, line, snippet in violations)
    )
