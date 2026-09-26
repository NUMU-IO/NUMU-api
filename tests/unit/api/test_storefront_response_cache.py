"""Whole-response cache for hot storefront reads: hit, bypass, never-cache, bust."""

import fnmatch

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.api.middleware import storefront_response_cache as mw
from src.infrastructure.cache import response_cache

STORE = "a1390eba-3607-4ad2-8a70-1359f38e1f31"
OTHER = "98eacd6b-40b6-4261-918b-159ebef99518"


class FakeRedis:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def scan_iter(self, match):
        for k in list(self.data):
            if fnmatch.fnmatch(k, match):
                yield k

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


@pytest.fixture
def env(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(mw, "response_cache_redis", lambda: fake)
    monkeypatch.setattr(response_cache, "response_cache_redis", lambda: fake)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if request.query_params.get("status") == "500":
            return JSONResponse({"n": calls["n"]}, status_code=500)
        resp = JSONResponse({"n": calls["n"]})
        if request.query_params.get("cookie"):
            resp.set_cookie("c", "1")
        return resp

    app = Starlette(
        routes=[
            Route("/api/v1/storefront/store/{sid}/products", handler),
            Route("/api/v1/storefront/store/{sid}/products/{slug}", handler),
            Route("/api/v1/storefront/store/{sid}/categories", handler),
            Route("/api/v1/storefront/store/{sid}/cart", handler),
        ]
    )
    app.add_middleware(mw.StorefrontResponseCacheMiddleware)
    return TestClient(app), calls, fake


def test_second_read_is_replayed_without_running_the_app(env):
    client, calls, _ = env
    url = f"/api/v1/storefront/store/{STORE}/products?page=1&limit=20"
    first = client.get(url)
    second = client.get(url)
    assert first.json() == second.json() == {"n": 1}
    assert calls["n"] == 1
    assert second.headers["x-numu-cache"] == "hit"
    # Query order does not matter.
    assert client.get(
        f"/api/v1/storefront/store/{STORE}/products?limit=20&page=1"
    ).json() == {"n": 1}


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        (f"/api/v1/storefront/store/{STORE}/products?search=x", {}),
        (f"/api/v1/storefront/store/{STORE}/products?fields=id", {}),
        (f"/api/v1/storefront/store/{STORE}/categories", {"authorization": "Bearer t"}),
        (f"/api/v1/storefront/store/{STORE}/categories", {"x-preview-token": "p"}),
        (f"/api/v1/storefront/store/{STORE}/categories", {"if-none-match": '"e"'}),
        (f"/api/v1/storefront/store/{STORE}/cart", {}),
        (f"/api/v1/storefront/store/{STORE}/products?status=500", {}),
        (f"/api/v1/storefront/store/{STORE}/products?cookie=1", {}),
    ],
)
def test_bypassed_or_never_cached(env, path, headers):
    client, calls, _ = env
    client.get(path, headers=headers)
    client.get(path, headers=headers)
    assert calls["n"] == 2


def test_encodings_are_cached_separately(env):
    client, calls, _ = env
    url = f"/api/v1/storefront/store/{STORE}/categories"
    client.get(url, headers={"accept-encoding": "gzip"})
    client.get(url, headers={"accept-encoding": "identity"})
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_bust_drops_only_that_store(env):
    client, calls, _ = env
    client.get(f"/api/v1/storefront/store/{STORE}/categories")
    client.get(f"/api/v1/storefront/store/{OTHER}/categories")
    await response_cache.bust_storefront_responses(STORE.upper())
    client.get(f"/api/v1/storefront/store/{STORE}/categories")
    client.get(f"/api/v1/storefront/store/{OTHER}/categories")
    assert calls["n"] == 3
