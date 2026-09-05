"""No module-level import cycles inside ``src``.

**Why this exists.** The carrier registry once imported a constant from
the carrier resolver at module scope while the resolver imported the
registry at module scope. Importing the registry first worked; importing
the *resolver* first raised ``ImportError``. Nothing in the suite caught
it — it surfaced only because a throwaway script happened to import them
in the unlucky order.

That is the failure mode this guards: a cycle is invisible until
something imports the two modules in the wrong order, and by then it is a
500 on a route nobody tested that way.

Static, not dynamic. Re-importing all 1,400 modules under ``src`` in a
fresh state would be far too slow to run every suite; parsing them is
fast and catches exactly this class of bug.

**Only module-level imports count.** A deferred import inside a function
cannot cycle, and this codebase uses them deliberately — the registry's
provider factories import their SDKs lazily so that importing the
registry does not drag in every carrier. Function bodies are skipped for
that reason; class bodies are not, because they execute at import time.
"""

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
PACKAGE = "src"


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(SRC.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _toplevel_imports(tree: ast.Module) -> set[str]:
    """Every ``src.*`` module imported at import-execution time.

    Function bodies are skipped: an import inside a function runs on
    call, not on import, so it cannot participate in a cycle. Class
    bodies are included — they execute when the module does.
    """
    found: set[str] = set()

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue  # deferred — cannot cycle
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name.startswith(PACKAGE):
                        found.add(alias.name)
            elif isinstance(child, ast.ImportFrom):
                # Relative imports never appear in this codebase; absolute
                # `from src.x.y import z` does.
                if child.module and child.module.startswith(PACKAGE):
                    found.add(child.module)
            walk(child)

    walk(tree)
    return found


def _build_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - would fail elsewhere first
            continue
        graph[_module_name(path)] = _toplevel_imports(tree)
    return graph


def _resolve(target: str, known: set[str]) -> str | None:
    """Map an imported name onto a module we actually have.

    ``from src.a.b import C`` names a symbol, not a module, so walk up
    until something resolves.
    """
    parts = target.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in known:
            return candidate
        parts.pop()
    return None


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Every import cycle, via depth-first search."""
    known = set(graph)
    edges = {
        mod: {
            resolved
            for target in targets
            if (resolved := _resolve(target, known)) and resolved != mod
        }
        for mod, targets in graph.items()
    }

    cycles: list[list[str]] = []
    seen_cycles: set[frozenset[str]] = set()
    visiting: list[str] = []
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in visiting:
            cycle = visiting[visiting.index(node) :] + [node]
            key = frozenset(cycle)
            if key not in seen_cycles:
                seen_cycles.add(key)
                cycles.append(cycle)
            return
        visiting.append(node)
        for neighbour in sorted(edges.get(node, ())):
            visit(neighbour)
        visiting.pop()
        done.add(node)

    for module in sorted(edges):
        visit(module)
    return cycles


def test_no_module_level_import_cycles():
    """A cycle breaks whichever module is imported first, at random.

    If this fails, the fix is usually to move the shared thing to
    whichever module owns it — the registry/resolver cycle was resolved by
    moving the operation-to-capability map into the registry, since it is
    carrier data — or to defer the import into the function that uses it.
    """
    cycles = _find_cycles(_build_graph())
    assert not cycles, "Module-level import cycles:\n" + "\n".join(
        "  " + " -> ".join(c) for c in cycles
    )


def test_the_detector_actually_detects():
    """A guard that cannot fail is not a guard.

    Verified against a synthetic cycle rather than trusting the traversal.
    """
    graph = {"src.a": {"src.b"}, "src.b": {"src.c"}, "src.c": {"src.a"}}
    assert _find_cycles(graph)

    straight = {"src.a": {"src.b"}, "src.b": {"src.c"}, "src.c": set()}
    assert not _find_cycles(straight)


def test_function_level_imports_are_ignored():
    """Deferred imports cannot cycle, and this codebase relies on that.

    The registry's provider factories import carrier SDKs inside the
    function so that importing the registry does not pull in every SDK.
    Counting those as edges would flag a cycle that cannot happen.
    """
    tree = ast.parse(
        "from src.real import thing\n"
        "def f():\n"
        "    from src.deferred import other\n"
        "    return other\n"
    )
    assert _toplevel_imports(tree) == {"src.real"}


def test_class_body_imports_are_counted():
    """Unlike function bodies, a class body runs at import time."""
    tree = ast.parse("class A:\n    from src.at_import import x\n")
    assert _toplevel_imports(tree) == {"src.at_import"}
