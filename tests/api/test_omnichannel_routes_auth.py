"""Request-level regression for the omnichannel (Inbox) routes.

These 19 routes were mounted with no dependency and none of their handlers
loaded the caller, so anyone holding a store id could read a store's customer
DMs, send messages as the merchant, and connect or delete its channels. Store
ids are not secret — the storefront hands them out.

`test_store_routes_require_auth.py` proves structurally that every store route
declares an auth dependency. This file proves the behaviour a caller actually
sees:

  * no credentials                      -> 401, before any handler runs
  * a merchant who does NOT own the store -> 403 (tenant isolation)
  * the store's owner                   -> gets past auth
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies.auth import require_store_owner
from src.api.dependencies.repositories import get_store_repository
from src.main import app

STORE = uuid4()
THREAD = uuid4()
CONNECTION = uuid4()

# Every route the fix covers, exactly as the route walk listed them on dev
# (which was also production) before the fix.
ROUTES = [
    ("DELETE", f"/api/v1/stores/{STORE}/channels/{CONNECTION}"),
    ("DELETE", f"/api/v1/stores/{STORE}/threads/{THREAD}/customer"),
    ("GET", f"/api/v1/stores/{STORE}/catalog/mappings"),
    ("GET", f"/api/v1/stores/{STORE}/channels/"),
    ("GET", f"/api/v1/stores/{STORE}/threads/"),
    ("GET", f"/api/v1/stores/{STORE}/threads/{THREAD}"),
    ("GET", f"/api/v1/stores/{STORE}/threads/{THREAD}/messages/"),
    ("GET", f"/api/v1/stores/{STORE}/whatsapp/"),
    ("POST", f"/api/v1/stores/{STORE}/capi/event"),
    ("POST", f"/api/v1/stores/{STORE}/catalog/sync"),
    ("POST", f"/api/v1/stores/{STORE}/channels/callback"),
    ("POST", f"/api/v1/stores/{STORE}/channels/connect"),
    ("POST", f"/api/v1/stores/{STORE}/channels/connect-assets"),
    ("POST", f"/api/v1/stores/{STORE}/channels/{CONNECTION}/sync-history"),
    ("POST", f"/api/v1/stores/{STORE}/threads/{THREAD}/customer"),
    ("POST", f"/api/v1/stores/{STORE}/threads/{THREAD}/messages/send"),
    ("POST", f"/api/v1/stores/{STORE}/threads/{THREAD}/read"),
    ("POST", f"/api/v1/stores/{STORE}/threads/{THREAD}/resolve"),
    ("POST", f"/api/v1/stores/{STORE}/whatsapp/"),
]


def test_the_list_matches_the_audit():
    assert len(ROUTES) == 19


@pytest.fixture
def client():
    with TestClient(app) as c:
        # No cookies either: the hub authenticates with a bearer token, and a
        # stray session cookie would make "no credentials" untrue.
        c.cookies.clear()
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_no_credentials_is_refused(client, method, path):
    response = client.request(method, path, json={})
    assert response.status_code == 401, (method, path, response.text[:200])


def _store_owned_by(owner_id):
    store = SimpleNamespace(id=STORE, owner_id=owner_id, tenant_id=None)

    class _Repo:
        async def get_by_id(self, store_id):
            return store if store_id == STORE else None

    return lambda: _Repo()


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_another_merchant_is_refused(client, method, path):
    # A real store owner, authenticated — just not the owner of THIS store.
    app.dependency_overrides[require_store_owner] = lambda: uuid4()
    app.dependency_overrides[get_store_repository] = _store_owned_by(uuid4())

    response = client.request(method, path, json={})

    assert response.status_code == 403, (method, path, response.text[:200])


def test_the_owner_gets_past_auth(client):
    owner = uuid4()
    app.dependency_overrides[require_store_owner] = lambda: owner
    app.dependency_overrides[get_store_repository] = _store_owned_by(owner)

    # The retired CAPI route answers 410 once auth passes, so reaching it
    # proves the dependency admitted the owner without needing any data.
    response = client.post(f"/api/v1/stores/{STORE}/capi/event", json={})

    assert response.status_code == 410, response.text[:200]
