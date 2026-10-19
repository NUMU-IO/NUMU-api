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


async def test_whatsapp_check_sends_the_trimmed_token_in_the_header(monkeypatch):
    import httpx

    from src.infrastructure.external_services.gateway_validators import (
        communication_validators as cv,
    )

    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "1", "name": "n"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        cv.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
    await cv.WhatsAppValidator().validate({
        "access_token": "EAABtok\n",
        "phone_number_id": "1",
        "business_account_id": "2",
    })
    assert len(seen) == 2
    assert all(r.headers["Authorization"] == "Bearer EAABtok" for r in seen)
    assert not any("EAABtok" in str(r.url) for r in seen)


async def test_verify_connection_sends_the_trimmed_token_in_the_header(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    import httpx

    import src.infrastructure.external_services.secrets.secrets_manager as sm
    from src.api.v1.routes.stores import settings as routes

    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"name": "Store", "is_active": True})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
    monkeypatch.setattr(
        routes, "_meta_cfg", lambda _store: {"pixel_id": "1737914750690516"}
    )
    cred = SimpleNamespace(
        is_active=True, credentials_encrypted="x", encryption_key_id="k"
    )
    monkeypatch.setattr(routes, "_get_capi_credential", AsyncMock(return_value=cred))
    secrets = MagicMock()
    secrets.decrypt = AsyncMock(return_value={"access_token": "EAABtok\n"})
    monkeypatch.setattr(sm, "get_secrets_manager", lambda: secrets)

    store = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    await routes.verify_meta_tracking_connection(store=store, db=AsyncMock())

    assert len(seen) == 1
    assert seen[0].headers["Authorization"] == "Bearer EAABtok"
    assert "EAABtok" not in str(seen[0].url)
