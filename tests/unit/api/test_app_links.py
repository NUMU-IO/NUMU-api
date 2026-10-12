"""BIS-K-05: ``numueg.app/a/<slug>/<rest>`` reaches the app's resolver and
302s where it says; anything else lands on the apex site; ``/o/`` is unchanged.
Also: the app fronts' subdomains cannot be taken as store subdomains."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.v1.routes import app_links
from src.api.v1.routes.app_links import APEX_FALLBACK
from src.api.v1.routes.order_redirect import router as order_redirect_router
from src.core.reserved_subdomains import is_reserved_subdomain
from src.infrastructure.database.connection import get_admin_db_session


@pytest.fixture
def client(monkeypatch):
    async def resolve(db, rest):
        return f"https://shop.example.com/products/{rest}" if rest != "gone" else None

    async def broken(db, rest):
        raise RuntimeError("boom")

    monkeypatch.setitem(app_links.APP_LINKS, "bis-test", resolve)
    monkeypatch.setitem(app_links.APP_LINKS, "broken-app", broken)
    app = FastAPI()
    app.include_router(app_links.router)
    app.include_router(order_redirect_router)

    async def no_db():
        yield None

    app.dependency_overrides[get_admin_db_session] = no_db
    return TestClient(app)


def _location(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 302
    return r.headers["location"]


def test_a_registered_app_link_goes_where_its_resolver_says(client):
    assert _location(client, "/a/bis-test/shirt/t0k3n") == (
        "https://shop.example.com/products/shirt/t0k3n"
    )


@pytest.mark.parametrize(
    "path", ["/a/unknown-app/x", "/a/bis-test/gone", "/a/broken-app/x"]
)
def test_unknown_unresolved_or_failing_links_land_on_the_apex(client, path):
    assert _location(client, path) == APEX_FALLBACK


def test_order_links_are_unchanged(client):
    assert _location(client, "/o/vionne/abc") == "https://vionne.numueg.app/track/abc"


@pytest.mark.parametrize(
    "slug",
    [
        "back-in-stock",
        "order-tracking",
        "reviews",
        "ai-catalog",
        "smart-search",
        "product-options",
        "flow",
        "affiliate",
        "product-quiz",
    ],
)
def test_app_front_subdomains_are_reserved(slug):
    assert is_reserved_subdomain(slug)
