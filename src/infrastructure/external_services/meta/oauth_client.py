"""Wave 3 Phase 17 — Meta Business OAuth client.

Async wrapper around Meta's Graph OAuth endpoints. Replaces the
paste-Pixel-ID + paste-CAPI-token UX with a "Connect with Meta
Business" button: the merchant grants permissions in Meta, NUMU
receives an access token + the list of pixels/pages/catalogs they
own, and the settings auto-populate.

**External dependency status (2026-05-17):** this code is complete
but DORMANT until the NUMU Meta App passes App Review for the
``ads_management`` + ``catalog_management`` scopes (~2–6 week
external process). The route handler at
``api/v1/routes/oauth/meta.py`` returns a clear "OAuth not yet
available" error until the env vars are set, so deploying this code
to production is safe — it's gated by configuration.

Scopes required (per the plan):
  * ``ads_management`` — list pixels, get EMQ scores from Marketing API
  * ``business_management`` — list System Users, generate long-lived tokens
  * ``catalog_management`` — create / update product catalog feeds
  * ``pages_show_list`` — list Facebook Pages the merchant owns
  * ``instagram_basic`` — link an Instagram business account
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)

# Scopes the merchant grants to NUMU. Listed in the same order Meta
# displays them in the consent screen so the request stays predictable
# and easy to support.
META_OAUTH_SCOPES: tuple[str, ...] = (
    "ads_management",
    "business_management",
    "catalog_management",
    "pages_show_list",
    "instagram_basic",
)


@dataclass(frozen=True)
class MetaOAuthTokens:
    """Result of a successful token exchange."""

    access_token: str
    token_type: str  # "bearer"
    # Meta returns expires_in for short-lived tokens only. System User
    # tokens are non-expiring — we still rotate every 60 days for safety.
    expires_in: int | None


@dataclass(frozen=True)
class MetaBusinessResources:
    """The pixels / pages / catalogs the merchant's token can access.

    Returned by ``list_business_resources`` so the merchant-hub UI can
    render a picker when the merchant owns more than one of each.
    """

    pixels: list[dict[str, Any]]  # [{"id": "...", "name": "..."}]
    pages: list[dict[str, Any]]
    catalogs: list[dict[str, Any]]
    business_id: str | None


class MetaOAuthError(Exception):
    """Raised when Meta returns a 4xx from any OAuth endpoint."""


class MetaOAuthClient:
    """Stateless async client for Meta's Graph OAuth + lookup endpoints.

    Construct with the NUMU Meta App's app_id + app_secret (env vars
    NUMU_META_APP_ID / NUMU_META_APP_SECRET). Each method is a thin
    httpx wrapper that surfaces Meta's error envelope as
    ``MetaOAuthError`` for callers to handle / log.
    """

    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        api_version: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id or getattr(settings, "meta_app_id", None) or ""
        self.app_secret = app_secret or getattr(settings, "meta_app_secret", None) or ""
        self.api_version = api_version or getattr(
            settings, "meta_graph_api_version", "v21.0"
        )
        self._client = client

    @property
    def is_configured(self) -> bool:
        """True iff the NUMU Meta App credentials are set in env."""
        return bool(self.app_id and self.app_secret)

    def authorization_url(self, *, redirect_uri: str, state: str) -> str:
        """Build the URL the user is redirected to for consent.

        ``state`` is a CSRF token the route handler generates per-attempt
        and verifies on the callback. Caller is responsible for storing
        it in the session and rejecting callbacks where it doesn't match.
        """
        scope = ",".join(META_OAUTH_SCOPES)
        params = {
            "client_id": self.app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": scope,
            # ``rerequest`` lets us re-prompt for scopes the user denied
            # previously — important when we add scopes in a release.
            "auth_type": "rerequest",
        }
        from urllib.parse import urlencode

        return f"https://www.facebook.com/{self.api_version}/dialog/oauth?" + urlencode(
            params
        )

    async def exchange_code_for_token(
        self, *, code: str, redirect_uri: str
    ) -> MetaOAuthTokens:
        """Trade the authorization code for a short-lived access token.

        Caller should immediately call ``upgrade_to_long_lived_token``
        to swap the 1-hour token for the ~60-day version.
        """
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.get(
                f"https://graph.facebook.com/{self.api_version}/oauth/access_token",
                params={
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            )
            return _parse_token_response(resp)
        finally:
            if self._client is None:
                await client.aclose()

    async def upgrade_to_long_lived_token(
        self, *, short_lived_token: str
    ) -> MetaOAuthTokens:
        """Upgrade a 1-hour token to a ~60-day long-lived token.

        For pixel + CAPI access we then exchange this further for a
        non-expiring System User token via ``mint_system_user_token``.
        """
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.get(
                f"https://graph.facebook.com/{self.api_version}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "fb_exchange_token": short_lived_token,
                },
            )
            return _parse_token_response(resp)
        finally:
            if self._client is None:
                await client.aclose()

    async def list_business_resources(
        self, *, access_token: str
    ) -> MetaBusinessResources:
        """Enumerate pixels / pages / catalogs the token can access.

        Each is a list of ``{"id": "...", "name": "..."}`` dicts the
        merchant-hub UI renders as a picker. When the list has exactly
        one entry, the route handler auto-selects it (the common case).
        """
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            # Resolve the business the user belongs to first — pixels &
            # catalogs are owned by businesses, not users.
            businesses_resp = await client.get(
                f"https://graph.facebook.com/{self.api_version}/me/businesses",
                params={"access_token": access_token, "fields": "id,name"},
            )
            businesses = _parse_data_response(businesses_resp)
            business_id = str(businesses[0]["id"]) if businesses else None

            pixels: list[dict[str, Any]] = []
            catalogs: list[dict[str, Any]] = []
            if business_id:
                pixels_resp = await client.get(
                    f"https://graph.facebook.com/{self.api_version}/{business_id}/adspixels",
                    params={"access_token": access_token, "fields": "id,name"},
                )
                pixels = _parse_data_response(pixels_resp)

                catalogs_resp = await client.get(
                    f"https://graph.facebook.com/{self.api_version}/{business_id}/owned_product_catalogs",
                    params={"access_token": access_token, "fields": "id,name"},
                )
                catalogs = _parse_data_response(catalogs_resp)

            pages_resp = await client.get(
                f"https://graph.facebook.com/{self.api_version}/me/accounts",
                params={"access_token": access_token, "fields": "id,name"},
            )
            pages = _parse_data_response(pages_resp)

            return MetaBusinessResources(
                pixels=pixels, pages=pages, catalogs=catalogs, business_id=business_id
            )
        finally:
            if self._client is None:
                await client.aclose()

    async def revoke_permissions(self, *, access_token: str) -> bool:
        """Revoke the merchant's grant of the NUMU Meta App, server-side.

        Posts ``DELETE /me/permissions`` which atomically removes ALL
        scopes the user previously granted. Returns True on success
        (Meta confirms removal) so the caller can audit. False on any
        failure — caller should still clear the local credential row +
        store settings, because once the merchant clicks Disconnect
        they expect NUMU to stop using their data regardless of what
        Meta's API does.

        Best-effort by design: 4xx (token already invalid, permissions
        already revoked) and 5xx (Meta outage) both return False
        without raising. The local cleanup is the authoritative
        operation; the Meta-side call is courtesy so the merchant's
        Meta Business Settings → Apps page also shows the disconnect.
        """
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.delete(
                f"https://graph.facebook.com/{self.api_version}/me/permissions",
                params={"access_token": access_token},
            )
            if resp.status_code >= 400:
                logger.warning(
                    "meta_revoke_permissions_failed",
                    extra={
                        "status": resp.status_code,
                        "body": resp.text[:300],
                    },
                )
                return False
            try:
                body = resp.json()
            except Exception:
                body = {}
            # Meta returns ``{"success": true}`` on a successful revoke.
            return bool(body.get("success")) if isinstance(body, dict) else True
        except httpx.HTTPError as exc:
            logger.warning(
                "meta_revoke_permissions_http_error",
                extra={"error": str(exc)[:300]},
            )
            return False
        finally:
            if self._client is None:
                await client.aclose()


def _parse_token_response(resp: httpx.Response) -> MetaOAuthTokens:
    if resp.status_code >= 400:
        raise MetaOAuthError(f"Meta OAuth error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    return MetaOAuthTokens(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data.get("expires_in"),
    )


def _parse_data_response(resp: httpx.Response) -> list[dict[str, Any]]:
    """Parse a paginated list response. v1: takes only the first page
    (Meta returns 100 entries per page; merchants with >100 pixels are
    vanishingly rare). Add cursor pagination in v1.1 if needed."""
    if resp.status_code >= 400:
        # Fail-open at the list level — partial results beat no results
        # for picker UIs (the merchant can paste an ID manually if their
        # resource is missing).
        logger.warning(
            "meta_oauth_list_resources_failed",
            extra={"status": resp.status_code, "body": resp.text[:300]},
        )
        return []
    return resp.json().get("data") or []
