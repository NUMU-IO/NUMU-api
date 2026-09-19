from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.api.middleware.cors import PublicReadCORSMiddleware

SPEC = "/api/v1/public/openapi.json"
APIDOG = {"Origin": "https://app.apidog.com"}


def _client() -> TestClient:
    app = FastAPI()

    @app.get(SPEC)
    def spec():
        return {"openapi": "3.1.0"}

    @app.get("/api/v1/auth/me")
    def me():
        return {}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://merchant.numueg.app"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["Authorization"],
    )
    app.add_middleware(PublicReadCORSMiddleware)
    return TestClient(app)


def test_any_site_can_read_the_public_contract():
    client = _client()

    preflight = client.options(
        SPEC,
        headers={
            **APIDOG,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-apidog",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert preflight.headers["access-control-allow-headers"] == "x-apidog"

    response = client.get(SPEC, headers=APIDOG)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_credentialed_origins_still_get_no_credentials_on_the_contract():
    response = _client().get(SPEC, headers={"Origin": "https://merchant.numueg.app"})

    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_everything_else_keeps_the_allow_list():
    preflight = _client().options(
        "/api/v1/auth/me",
        headers={**APIDOG, "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers
