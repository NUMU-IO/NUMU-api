"""A stdlib ``logging.getLogger`` logger takes no field keywords.

``logger.info("event", intent_id=...)`` on a stdlib logger raises
``TypeError: Logger._log() got an unexpected keyword argument``. Inside an
``except`` block that turns a handled error into a crash: on 2026-10-13 the
InstaPay expiry sweep failed on every run this way. Field keywords belong on
``src.core.logging.get_logger``.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
STDLIB_KWARGS = {"exc_info", "stack_info", "stacklevel", "extra"}
LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}


def _stdlib_logger_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "getLogger"
                and isinstance(func.value, ast.Name)
                and func.value.id == "logging"
            ):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def test_no_stdlib_logger_call_passes_field_keywords():
    bad = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = _stdlib_logger_names(tree)
        if not names:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LEVELS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in names
            ):
                keys = [
                    k.arg or "**" for k in node.keywords if k.arg not in STDLIB_KWARGS
                ]
                if keys:
                    bad.append(f"{path.relative_to(SRC)}:{node.lineno} {keys}")
    assert bad == [], "stdlib logger called with field keywords:\n" + "\n".join(bad)
