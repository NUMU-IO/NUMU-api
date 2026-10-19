"""No Meta Graph request may carry a token or the app secret in its URL.

URLs end up in logs, traces and error text (see test_telemetry_secrets.py),
so tokens travel in the Authorization header and the OAuth code exchange
posts its secrets in the body. Meta's debug_token takes no POST, so its
``input_token`` is the one value still allowed in a query string.
"""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
GRAPH = re.compile(r"graph\.facebook\.com|graph_api_base|_graph_url|GRAPH_API_URL")
SECRETS = {"access_token", "client_secret", "fb_exchange_token"}


def _keys(node: ast.AST | None) -> set[str]:
    if isinstance(node, ast.Dict):
        return {k.value for k in node.keys if isinstance(k, ast.Constant)}
    return set()


def _secret_params(tree: ast.AST) -> set[int]:
    """Line numbers of calls whose ``params`` hold a secret, literal or built."""
    lines: set[int] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        built: dict[str, set[str]] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        built.setdefault(target.id, set()).update(_keys(node.value))
                    elif (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and isinstance(target.slice, ast.Constant)
                    ):
                        built.setdefault(target.value.id, set()).add(target.slice.value)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "params":
                    continue
                keys = _keys(kw.value)
                if isinstance(kw.value, ast.Name):
                    keys |= built.get(kw.value.id, set())
                if keys & SECRETS:
                    lines.add(node.lineno)
    return lines


def test_no_meta_request_puts_a_secret_in_its_url():
    leaks = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if GRAPH.search(text):
            rel = path.relative_to(SRC.parent).as_posix()
            leaks += [f"{rel}:{n}" for n in sorted(_secret_params(ast.parse(text)))]
    assert leaks == []
