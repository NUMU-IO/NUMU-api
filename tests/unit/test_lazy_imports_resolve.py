"""Every ``src.`` import resolves, including the ones inside function bodies.

A broken module-level import fails at boot; one inside a function fails only
when that function runs. Scheduled tasks import lazily, so on 2026-10-15..18
the worker log showed three of them failing on every run:
``onboarding_nudge_tasks`` imported ``src.infrastructure.messaging.redis_client``,
which never existed, and ``ai_insights_tasks`` imported
``async_session_factory``, which ``database/connection.py`` does not define.
"""

import ast
import importlib
import importlib.util
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# Broken before this test existed, and left for the owner because fixing them
# changes live behaviour. Do not add to this list: fix the import.
KNOWN_BROKEN = {
    # build_external_theme (the legacy GitHub theme build) fails at its last
    # step; fixing it writes theme_settings on live stores again.
    (
        "src/infrastructure/messaging/tasks/theme_build_tasks.py",
        "src.infrastructure.repositories.store_repository"
        " has no SQLAlchemyStoreRepository",
    ),
    # Never mounted: its package __init__ swallows this ImportError.
    (
        "src/api/v1/routes/tenant/configuration/merchant_routes.py",
        "src.api.dependencies.auth has no get_current_user",
    ),
    # Nothing imports this entity; Address is in src.core.value_objects.address.
    ("src/core/entities/location.py", "src.core.entities.address has no Address"),
}


def _find_spec(name: str):
    try:
        return importlib.util.find_spec(name)
    except ImportError:  # a missing parent package
        return None


def _src_imports():
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level:
                if node.module == "src" or node.module.startswith("src."):
                    names = [a.name for a in node.names if a.name != "*"]
                    yield path, node.lineno, node.module, names
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src."):
                        yield path, node.lineno, alias.name, []


def test_every_src_import_resolves():
    bad = []
    for path, line, module, names in _src_imports():
        rel = path.relative_to(SRC.parent).as_posix()
        problems = []
        if _find_spec(module) is None:
            problems.append(f"no module {module}")
        else:
            loaded = importlib.import_module(module)
            problems += [
                f"{module} has no {name}"
                for name in names
                if not hasattr(loaded, name) and _find_spec(f"{module}.{name}") is None
            ]
        bad += [f"{rel}:{line} {p}" for p in problems if (rel, p) not in KNOWN_BROKEN]
    assert bad == [], "imports that fail when they run:\n" + "\n".join(bad)
