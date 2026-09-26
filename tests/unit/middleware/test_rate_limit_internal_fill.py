"""Storefront cache fills (internal token, no shopper IP) skip only the general tier."""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.api.middleware import rate_limit

TOKEN = "t" * 48


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "internal_service_token", TOKEN)
    monkeypatch.setattr(rate_limit.settings, "rate_limit_enabled", True)
    checked: list[tuple[str, str]] = []

    async def fake_check(bucket, tier, limit):
        checked.append((bucket, tier))
        return True, 1, 0

    monkeypatch.setattr(rate_limit, "_check_rate_limit", fake_check)

    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/{path:path}", ok, methods=["GET", "POST"])])
    app.add_middleware(rate_limit.RateLimitMiddleware)
    return TestClient(app), checked


def test_internal_fill_without_shopper_ip_skips_the_general_tier(client):
    c, checked = client
    c.get(
        "/api/v1/storefront/store/abc/products",
        headers={"x-internal-service-token": TOKEN},
    )
    assert [t for _, t in checked if t == "general"] == []


def test_shopper_ip_or_no_token_is_still_limited(client):
    c, checked = client
    c.get(
        "/api/v1/storefront/store/abc/products",
        headers={"x-internal-service-token": TOKEN, "x-forwarded-for": "1.2.3.4"},
    )
    c.get("/api/v1/storefront/store/abc/products")
    c.get(
        "/api/v1/storefront/store/abc/products",
        headers={"x-internal-service-token": "wrong"},
    )
    assert [t for _, t in checked].count("general") == 3
    assert checked[0][0] == "1.2.3.4"


def test_stricter_tiers_still_apply_to_internal_calls(client):
    c, checked = client
    c.post(
        "/api/v1/storefront/store/abc/checkout",
        headers={"x-internal-service-token": TOKEN},
    )
    assert checked and checked[0][1] != "general"
