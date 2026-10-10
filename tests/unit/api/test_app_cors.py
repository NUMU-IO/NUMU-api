"""BIS-K-15: an app front's origin (APP_CORS_ORIGINS) gets CORS on
``/api/v1/apps/*`` only, never with credentials, and the main policy
(CORS_ORIGINS, with credentials) is unchanged everywhere else and never
answers for the app routes."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import cors

APP = "https://back-in-stock.example.com"
HUB = "https://merchant.example.com"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cors.settings, "cors_origins", [HUB])
    monkeypatch.setattr(cors.settings, "app_cors_origins", [APP])
    monkeypatch.setattr(cors.settings, "debug", False)
    app = FastAPI()

    @app.get("/api/v1/apps/bis/ping")
    async def app_ping():
        return {}

    @app.get("/api/v1/stores/x")
    async def core():
        return {}

    cors.setup_cors(app)
    return TestClient(app)


def _cors(r):
    return {k: v for k, v in r.headers.items() if k.startswith("access-control-")}


def test_the_app_origin_reads_app_routes_without_credentials(client):
    r = client.get("/api/v1/apps/bis/ping", headers={"Origin": APP})

    assert r.headers["access-control-allow-origin"] == APP
    assert "access-control-allow-credentials" not in r.headers


def test_the_app_origin_preflight_is_answered_without_credentials(client):
    r = client.options(
        "/api/v1/apps/bis/ping",
        headers={
            "Origin": APP,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )

    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == APP
    assert r.headers["access-control-allow-headers"] == "authorization, content-type"
    assert "access-control-allow-credentials" not in r.headers


def test_the_app_origin_gets_nothing_on_core_routes(client):
    r = client.get("/api/v1/stores/x", headers={"Origin": APP})

    # Starlette's main policy always sends allow-credentials/expose-headers;
    # without Allow-Origin the browser still refuses the read.
    assert "access-control-allow-origin" not in r.headers


def test_the_hubs_credentialed_policy_never_answers_on_app_routes(client):
    r = client.get("/api/v1/apps/bis/ping", headers={"Origin": HUB})

    assert _cors(r) == {}


def test_an_unknown_origins_preflight_on_app_routes_is_refused(client):
    r = client.options(
        "/api/v1/apps/bis/ping",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_core_routes_keep_the_main_policy(client):
    r = client.get("/api/v1/stores/x", headers={"Origin": HUB})

    assert r.headers["access-control-allow-origin"] == HUB
    assert r.headers["access-control-allow-credentials"] == "true"
