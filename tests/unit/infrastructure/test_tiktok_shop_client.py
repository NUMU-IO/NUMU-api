"""Unit tests for the TikTok Shop + TikTok OAuth clients (pure pieces).

No network: covers request signing determinism, webhook HMAC verification,
authorize-URL shape, and the ``{code,message,data}`` envelope parser.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from src.infrastructure.external_services.tiktok.oauth_client import (
    TIKTOK_OAUTH_SCOPES,
    TikTokOAuthClient,
)
from src.infrastructure.external_services.tiktok.shop_client import (
    TikTokShopClient,
    TikTokShopError,
    _parse_envelope,
)


class TestTikTokOAuthClient:
    def test_is_configured(self):
        assert TikTokOAuthClient(app_id="a", app_secret="b").is_configured
        assert not TikTokOAuthClient(app_id="", app_secret="").is_configured

    def test_authorization_url_shape(self):
        c = TikTokOAuthClient(app_id="APP1", app_secret="s")
        url = c.authorization_url(
            redirect_uri="https://numueg.app/api/v1/oauth/tiktok/callback",
            state="csrf-store",
        )
        assert url.startswith("https://business-api.tiktok.com/portal/auth?")
        assert "app_id=APP1" in url
        assert "state=csrf-store" in url

    def test_scopes_present(self):
        assert "pixel_management" in TIKTOK_OAUTH_SCOPES


class TestShopSigning:
    def test_sign_is_deterministic_64_hex(self):
        c = TikTokShopClient(app_key="k", app_secret="s")
        params = {"app_key": "k", "timestamp": "1700000000", "shop_cipher": "x"}
        a = c._sign("/order/202309/orders", params, "")
        b = c._sign("/order/202309/orders", dict(reversed(list(params.items()))), "")
        assert a == b  # key order independent (sorted internally)
        assert len(a) == 64

    def test_sign_excludes_sign_and_access_token(self):
        c = TikTokShopClient(app_key="k", app_secret="s")
        base = {"app_key": "k", "timestamp": "1"}
        with_noise = {**base, "sign": "zzz", "access_token": "tok"}
        assert c._sign("/p", base, "") == c._sign("/p", with_noise, "")

    def test_body_changes_signature(self):
        c = TikTokShopClient(app_key="k", app_secret="s")
        p = {"app_key": "k", "timestamp": "1"}
        assert c._sign("/p", p, '{"a":1}') != c._sign("/p", p, '{"a":2}')


class TestWebhookVerify:
    def test_valid_signature_passes(self):
        c = TikTokShopClient(app_key="APPK", app_secret="SECR")
        body = b'{"type":"ORDER_STATUS_CHANGE","shop_id":"S1"}'
        good = hmac.new(
            b"SECR", (b"APPK" + body).decode().encode(), hashlib.sha256
        ).hexdigest()
        assert c.verify_webhook_signature(raw_body=body, signature=good)

    def test_bad_signature_fails(self):
        c = TikTokShopClient(app_key="APPK", app_secret="SECR")
        assert not c.verify_webhook_signature(raw_body=b"{}", signature="deadbeef")

    def test_unconfigured_rejects(self):
        c = TikTokShopClient(app_key="", app_secret="")
        assert not c.verify_webhook_signature(raw_body=b"{}", signature="anything")


class TestEnvelope:
    def test_success_returns_data(self):
        resp = httpx.Response(200, json={"code": 0, "message": "OK", "data": {"x": 1}})
        assert _parse_envelope(resp) == {"x": 1}

    def test_nonzero_code_raises(self):
        resp = httpx.Response(200, json={"code": 36004, "message": "bad", "data": {}})
        with pytest.raises(TikTokShopError):
            _parse_envelope(resp)

    def test_http_error_raises(self):
        resp = httpx.Response(500, text="boom")
        with pytest.raises(TikTokShopError):
            _parse_envelope(resp)
