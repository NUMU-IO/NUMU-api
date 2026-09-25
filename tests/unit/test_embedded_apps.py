"""Embedded apps (session token) and the storefront app proxy."""

import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.v1.routes.storefront import apps as storefront_apps
from src.application.services import app_proxy
from src.application.services.app_manifest import (
    ManifestV1,
    change_type,
    to_listing_manifest,
)
from src.application.services.app_tokens import session_token, sign_params
from tests.unit.test_app_manifest import GOOD

SECRET = "numu_cs_" + "s" * 43
STORE = uuid4()
USER = uuid4()


def _decode(token: str, **kw):
    return jwt.decode(
        token, SECRET, algorithms=["HS256"], audience="cid", issuer="numueg.app", **kw
    )


def test_session_token_claims():
    claims = _decode(
        session_token(
            SECRET, client_id="cid", user_id=USER, store_id=STORE, locale="en"
        )
    )
    assert claims["sub"] == str(USER)
    assert claims["dest"] == str(STORE)
    assert claims["locale"] == "en"
    assert claims["exp"] - claims["iat"] == 60
    assert len(claims["jti"]) == 32


def test_session_token_wrong_audience_or_secret():
    token = session_token(
        SECRET, client_id="cid", user_id=USER, store_id=STORE, locale="ar"
    )
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(token, SECRET, algorithms=["HS256"], audience="other")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "n" * 50, algorithms=["HS256"], audience="cid")


def test_session_token_expires(monkeypatch):
    past = datetime.now(UTC) - timedelta(seconds=61)
    monkeypatch.setattr(
        "src.application.services.app_tokens.datetime",
        type("D", (), {"now": staticmethod(lambda tz=None: past)}),
    )
    token = session_token(
        SECRET, client_id="cid", user_id=USER, store_id=STORE, locale="ar"
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        _decode(token)


def _manifest(**extra):
    m = copy.deepcopy(GOOD)
    m.update(extra)
    return m


def test_manifest_embedded_and_proxy_reach_the_listing():
    m = ManifestV1.model_validate(
        _manifest(
            embedded=True,
            embedded_path="/embedded",
            app_proxy={"subpath": "bosta-sync", "url": "https://app.example.com/px"},
        )
    ).model_dump(exclude_none=True)
    app = to_listing_manifest(m, developer_name="Dev")["app"]
    assert app["embedded"] is True
    assert app["embedded_path"] == "/embedded"
    assert app["app_proxy"]["url"] == "https://app.example.com/px"
    assert change_type(m, {**m, "app_proxy": None}) == "urls"


@pytest.mark.parametrize(
    "extra",
    [
        {"embedded": True, "embedded_path": "//evil.example"},
        {"embedded": True, "embedded_path": "relative"},
        {"embedded_path": "/x"},
        {"app_proxy": {"subpath": "other-app", "url": "https://app.example.com/px"}},
        {"app_proxy": {"subpath": "bosta-sync", "url": "http://app.example.com/px"}},
    ],
)
def test_manifest_rejects(extra):
    with pytest.raises(ValidationError):
        ManifestV1.model_validate(_manifest(**extra))


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(app_proxy, "assert_webhook_target", lambda url: None)


async def _forward(handler, **kw):
    args = {
        "base_url": "https://app.example.com/px",
        "secret": SECRET,
        "store_id": str(STORE),
        "slug": "bosta-sync",
        "path": "reviews/1",
        "method": "GET",
        "query": {"page": "2"},
        "headers": {
            "Cookie": "session=shopper",
            "Authorization": "Bearer x",
            "Accept": "text/html",
            "X-Forwarded-For": "1.2.3.4",
        },
        "body": b"",
        "transport": httpx.MockTransport(handler),
    }
    return await app_proxy.forward(**{**args, **kw})


async def test_proxy_signs_and_strips_credentials():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = request.url
        seen["headers"] = request.headers
        return httpx.Response(
            200,
            html="<p>hi</p>",
            headers={"Set-Cookie": "evil=1", "Cache-Control": "max-age=60"},
        )

    res = await _forward(handler)
    assert res.status == 200
    assert res.body == b"<p>hi</p>"
    assert "set-cookie" not in {k.lower() for k in res.headers}
    assert res.headers["Content-Security-Policy"].startswith("sandbox")
    assert res.headers["Cache-Control"] == "max-age=60"
    assert seen["url"].path == "/px/reviews/1"
    params = dict(seen["url"].params)
    assert params["store_id"] == str(STORE)
    assert params["path_prefix"] == "/apps/bosta-sync"
    assert params["page"] == "2"
    assert params["hmac"] == sign_params(params, SECRET)
    assert "cookie" not in seen["headers"]
    assert "authorization" not in seen["headers"]
    assert "x-forwarded-for" not in seen["headers"]
    assert seen["headers"]["accept"] == "text/html"


async def test_proxy_path_cannot_leave_the_app_host():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return httpx.Response(200, json={})

    await _forward(handler, path="//evil.example/%3Fx")
    assert seen["url"].host == "app.example.com"


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (
            httpx.Response(
                200, content=b"x", headers={"Content-Type": "image/svg+xml"}
            ),
            502,
        ),
        (
            httpx.Response(
                200, content=b"x", headers={"Content-Type": "application/pdf"}
            ),
            502,
        ),
        (httpx.Response(302, headers={"Location": "https://evil.example"}), 502),
        (httpx.Response(302, headers={"Location": "//evil.example"}), 502),
        (httpx.Response(302, headers={"Location": "/apps/bosta-sync/done"}), 302),
        (httpx.Response(200, content=b"x", headers={"Content-Type": "image/png"}), 200),
        (
            httpx.Response(
                200,
                content=b"x" * (app_proxy.MAX_BYTES + 1),
                headers={"Content-Type": "text/html"},
            ),
            502,
        ),
    ],
)
async def test_proxy_response_rules(response, status):
    res = await _forward(lambda request: response)
    assert res.status == status


async def test_proxy_timeout_is_504():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    assert (await _forward(handler)).status == 504


async def test_proxy_unsafe_target_is_502(monkeypatch):
    from src.core.url_guard import UnsafeUrlError

    def unsafe(url):
        raise UnsafeUrlError("private")

    monkeypatch.setattr(app_proxy, "assert_webhook_target", unsafe)
    assert (await _forward(lambda r: httpx.Response(200))).status == 502


async def test_proxy_request_body_limit():
    res = await _forward(
        lambda r: httpx.Response(200),
        method="POST",
        body=b"x" * (app_proxy.MAX_BYTES + 1),
    )
    assert res.status == 413


def test_proxy_route_not_installed_is_404(monkeypatch):
    async def none(store_id, slug):
        return None

    monkeypatch.setattr(storefront_apps, "_proxy_target", none)
    api = FastAPI()
    api.include_router(storefront_apps.router, prefix="/storefront/store/{store_id}")
    res = TestClient(api).get(f"/storefront/store/{STORE}/apps/nope/proxy/x")
    assert res.status_code == 404


def test_built_by_numu_flag_marks_the_listing_first_party():
    from src.api.v1.routes.stores.apps import _listing

    manifest = to_listing_manifest(copy.deepcopy(GOOD), developer_name="Acme")
    assert _listing(manifest).developer["is_first_party"] is False
    assert _listing(manifest, {"built_by_numu": False}).developer["name"] == "Acme"
    dev = _listing(manifest, {"built_by_numu": True}).developer
    assert dev["is_first_party"] is True
    assert dev["name"] == "NUMU"
    assert dev["support_email"] == manifest["developer"]["support_email"]
