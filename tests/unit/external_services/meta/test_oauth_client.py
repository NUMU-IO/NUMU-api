"""Wave 3 Phase 17 — Meta OAuth client unit tests.

Pins the parts of the client that don't require a real Meta App:
  * Authorization URL construction (scope set, params, CSRF state)
  * ``is_configured`` reflecting env-var presence
  * Token-response parsing (success + error envelopes)
  * Defensive list parsing (Meta 4xx → empty list, not raise)

Full OAuth exchange flow tests live in the integration suite once
the NUMU Meta App passes Review; until then those gates are env-var
guarded at the route layer.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from src.infrastructure.external_services.meta.oauth_client import (
    META_OAUTH_SCOPES,
    MetaOAuthClient,
    MetaOAuthError,
    _parse_data_response,
    _parse_token_response,
)


class TestIsConfigured:
    def test_unconfigured_when_both_missing(self):
        client = MetaOAuthClient(app_id="", app_secret="")
        assert client.is_configured is False

    def test_unconfigured_when_only_app_id_set(self):
        client = MetaOAuthClient(app_id="123", app_secret="")
        assert client.is_configured is False

    def test_configured_when_both_set(self):
        client = MetaOAuthClient(app_id="123", app_secret="secret")
        assert client.is_configured is True


class TestAuthorizationUrl:
    def _client(self) -> MetaOAuthClient:
        return MetaOAuthClient(
            app_id="numu_app_123",
            app_secret="dont_log_this",
            api_version="v21.0",
        )

    def test_url_targets_facebook_oauth_dialog(self):
        url = self._client().authorization_url(
            redirect_uri="https://numu.store/callback",
            state="csrf-state-abc",
        )
        assert url.startswith("https://www.facebook.com/v21.0/dialog/oauth?")

    def test_url_includes_client_id(self):
        url = self._client().authorization_url(redirect_uri="https://x", state="s")
        assert "client_id=numu_app_123" in url

    def test_url_includes_redirect_uri(self):
        url = self._client().authorization_url(
            redirect_uri="https://numu.store/cb", state="s"
        )
        # URL-encoded
        assert "redirect_uri=https%3A%2F%2Fnumu.store%2Fcb" in url

    def test_url_includes_state(self):
        url = self._client().authorization_url(
            redirect_uri="https://x", state="csrf-token-xyz"
        )
        assert "state=csrf-token-xyz" in url

    def test_url_lists_all_required_scopes(self):
        url = self._client().authorization_url(redirect_uri="https://x", state="s")
        # Scopes are comma-separated, URL-encoded as %2C
        for scope in META_OAUTH_SCOPES:
            assert scope in url

    def test_scopes_include_ads_and_catalog_management(self):
        # The two scopes that require App Review — pinned to catch
        # accidental removal that would silently break EMQ + Catalog.
        assert "ads_management" in META_OAUTH_SCOPES
        assert "catalog_management" in META_OAUTH_SCOPES

    def test_url_requests_rerequest_auth_type(self):
        # Lets us re-prompt for previously-denied scopes when we add
        # new ones in a release.
        url = self._client().authorization_url(redirect_uri="https://x", state="s")
        assert "auth_type=rerequest" in url

    def test_app_secret_not_in_url(self):
        # Defense against a copy-paste accident: app_secret must NEVER
        # appear in the redirect URL (it's only used on the server-side
        # token exchange).
        url = self._client().authorization_url(redirect_uri="https://x", state="s")
        assert "dont_log_this" not in url


class TestParseTokenResponse:
    def _resp(self, status: int, body: dict | str) -> httpx.Response:
        if isinstance(body, dict):
            import json

            return httpx.Response(status, content=json.dumps(body))
        return httpx.Response(status, content=body)

    def test_success_returns_token(self):
        resp = self._resp(
            200,
            {
                "access_token": "EAAB-abc",
                "token_type": "bearer",
                "expires_in": 5184000,  # ~60 days
            },
        )
        tokens = _parse_token_response(resp)
        assert tokens.access_token == "EAAB-abc"
        assert tokens.token_type == "bearer"
        assert tokens.expires_in == 5184000

    def test_expires_in_optional(self):
        # System User tokens are non-expiring — Meta omits expires_in.
        resp = self._resp(200, {"access_token": "EAAB-xyz", "token_type": "bearer"})
        tokens = _parse_token_response(resp)
        assert tokens.expires_in is None

    def test_token_type_defaults_to_bearer(self):
        resp = self._resp(200, {"access_token": "EAAB-xyz"})
        tokens = _parse_token_response(resp)
        assert tokens.token_type == "bearer"

    def test_4xx_raises_meta_oauth_error(self):
        resp = self._resp(
            400,
            {
                "error": {
                    "message": "Invalid OAuth access token",
                    "type": "OAuthException",
                    "code": 190,
                }
            },
        )
        with pytest.raises(MetaOAuthError) as exc_info:
            _parse_token_response(resp)
        assert "400" in str(exc_info.value)


class TestParseDataResponse:
    """List-resource parsing is fail-open — 4xx → empty list."""

    def test_success_returns_data_array(self):
        import json

        resp = httpx.Response(
            200,
            content=json.dumps({"data": [{"id": "1", "name": "Pixel A"}]}),
        )
        out = _parse_data_response(resp)
        assert out == [{"id": "1", "name": "Pixel A"}]

    def test_empty_data_returns_empty_list(self):
        import json

        resp = httpx.Response(200, content=json.dumps({"data": []}))
        assert _parse_data_response(resp) == []

    def test_missing_data_key_returns_empty_list(self):
        import json

        resp = httpx.Response(200, content=json.dumps({}))
        assert _parse_data_response(resp) == []

    def test_4xx_returns_empty_list_not_raise(self):
        # Partial picker > no picker — the merchant can paste the ID
        # manually if their resource is missing from the auto-list.
        resp = httpx.Response(
            500, content='{"error":{"message":"Server error","code":2}}'
        )
        assert _parse_data_response(resp) == []


@pytest.mark.asyncio
async def test_exchange_code_for_token_passes_correct_params():
    """The token-exchange request sends the documented Meta params."""
    captured: dict = {}

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aclose(self):
            return None

        async def get(self, url, params=None, **_):
            captured["url"] = url
            captured["params"] = params
            import json

            return httpx.Response(
                200,
                content=json.dumps({
                    "access_token": "T",
                    "token_type": "bearer",
                    "expires_in": 60,
                }),
            )

    stub = MagicMock(wraps=_StubClient())
    stub.get = _StubClient().get  # type: ignore[method-assign]
    stub.aclose = _StubClient().aclose  # type: ignore[method-assign]

    client = MetaOAuthClient(
        app_id="A", app_secret="S", api_version="v21.0", client=stub
    )
    tokens = await client.exchange_code_for_token(
        code="code123", redirect_uri="https://x/cb"
    )
    assert tokens.access_token == "T"
    assert captured["params"]["client_id"] == "A"
    assert captured["params"]["client_secret"] == "S"
    assert captured["params"]["redirect_uri"] == "https://x/cb"
    assert captured["params"]["code"] == "code123"
    assert "/v21.0/oauth/access_token" in captured["url"]
