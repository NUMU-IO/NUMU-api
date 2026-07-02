"""TikTok for Business OAuth client — sibling of ``meta/oauth_client``.

Async wrapper around TikTok's Marketing/Events API OAuth endpoints. Replaces
the paste-Pixel-Code + paste-Events-API-token UX with a "Connect with TikTok"
button: the merchant authorizes NUMU on TikTok, NUMU receives a long-lived
access token + the advertiser(s) + their pixels, and the settings auto-populate.

**External dependency status:** DORMANT until the NUMU TikTok App credentials
(``NUMU_TIKTOK_APP_ID`` / ``NUMU_TIKTOK_APP_SECRET``) are set. The route handler
(``api/v1/routes/oauth/tiktok.py``) returns 503 until then, so deploying this
code is safe — it's gated by configuration exactly like the Meta OAuth.

TikTok OAuth deltas vs Meta:
  * Consent screen is ``business-api.tiktok.com/portal/auth`` (app_id, not
    client_id; TikTok appends ``auth_code`` — not ``code`` — to the callback).
  * Token exchange is a POST to ``.../v1.3/oauth2/access_token/`` with a JSON
    body; the response is TikTok's ``{code, data:{...}}`` envelope.
  * Tokens are already long-lived — there's no short→long upgrade step.
  * Pixels are listed per advertiser via ``.../v1.3/pixel/list/``; the field
    that goes into the storefront SDK / Events API ``event_source_id`` is the
    ``pixel_code`` (aka Pixel Code), NOT the numeric ``pixel_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)

_BASE = "https://business-api.tiktok.com"
_API_VERSION = "v1.3"

# Scopes the merchant grants to NUMU. TikTok scopes are numeric IDs in the
# portal URL, but we keep readable names for logging/support. The actual
# scope set is configured on the TikTok App itself; the portal URL doesn't
# take a scope param the way Meta's does.
TIKTOK_OAUTH_SCOPES: tuple[str, ...] = (
    "pixel_management",
    "events_api",
    "advertiser_read",
)


@dataclass(frozen=True)
class TikTokOAuthTokens:
    """Result of a successful token exchange."""

    access_token: str
    # Advertiser ids the merchant authorized. Pixels are listed per advertiser.
    advertiser_ids: list[str]
    scope: list[str]


@dataclass(frozen=True)
class TikTokPixelResource:
    """One pixel the merchant's token can access.

    ``pixel_code`` is what the storefront SDK / Events API use as the
    ``event_source_id`` (and what the hub persists as ``pixel_id``).
    """

    pixel_id: str
    pixel_code: str
    name: str | None
    advertiser_id: str


class TikTokOAuthError(Exception):
    """Raised when TikTok returns an error from any OAuth endpoint."""


class TikTokOAuthClient:
    """Stateless async client for TikTok's Business OAuth + pixel lookup."""

    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id or getattr(settings, "tiktok_app_id", None) or ""
        self.app_secret = (
            app_secret or getattr(settings, "tiktok_app_secret", None) or ""
        )
        self._client = client

    @property
    def is_configured(self) -> bool:
        """True iff the NUMU TikTok App credentials are set in env."""
        return bool(self.app_id and self.app_secret)

    def authorization_url(self, *, redirect_uri: str, state: str) -> str:
        """Build the URL the user is redirected to for consent.

        ``state`` is a CSRF token the route handler generates per-attempt and
        verifies on the callback.
        """
        params = {
            "app_id": self.app_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{_BASE}/portal/auth?" + urlencode(params)

    async def exchange_code_for_token(self, *, auth_code: str) -> TikTokOAuthTokens:
        """Trade the authorization code for a (long-lived) access token.

        TikTok's ``redirect_uri`` is fixed on the App config, so — unlike Meta
        — it is NOT part of the token-exchange body.
        """
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.post(
                f"{_BASE}/open_api/{_API_VERSION}/oauth2/access_token/",
                json={
                    "app_id": self.app_id,
                    "secret": self.app_secret,
                    "auth_code": auth_code,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/json"},
            )
            data = _parse_envelope(resp)
            return TikTokOAuthTokens(
                access_token=data["access_token"],
                advertiser_ids=[str(a) for a in (data.get("advertiser_ids") or [])],
                scope=list(data.get("scope") or []),
            )
        finally:
            if self._client is None:
                await client.aclose()

    async def list_pixels(
        self, *, access_token: str, advertiser_ids: list[str]
    ) -> list[TikTokPixelResource]:
        """Enumerate pixels across the merchant's authorized advertisers.

        Returns a flat list the merchant-hub UI renders as a picker. When it
        has exactly one entry the route handler auto-selects it.
        """
        client = self._client or httpx.AsyncClient(timeout=15.0)
        out: list[TikTokPixelResource] = []
        try:
            for adv_id in advertiser_ids:
                resp = await client.get(
                    f"{_BASE}/open_api/{_API_VERSION}/pixel/list/",
                    params={"advertiser_id": adv_id},
                    headers={"Access-Token": access_token},
                )
                try:
                    data = _parse_envelope(resp)
                except TikTokOAuthError:
                    # Fail-open per advertiser — partial results beat none for
                    # a picker (the merchant can paste a Pixel Code manually).
                    logger.warning(
                        "tiktok_oauth_list_pixels_failed",
                        extra={"advertiser_id": adv_id},
                    )
                    continue
                for p in data.get("pixels") or []:
                    code = p.get("pixel_code") or p.get("code")
                    if not code:
                        continue
                    out.append(
                        TikTokPixelResource(
                            pixel_id=str(p.get("pixel_id") or code),
                            pixel_code=str(code),
                            name=p.get("pixel_name") or p.get("name"),
                            advertiser_id=adv_id,
                        )
                    )
        finally:
            if self._client is None:
                await client.aclose()
        return out


def _parse_envelope(resp: httpx.Response) -> dict[str, Any]:
    """Parse TikTok's ``{code, message, data}`` envelope.

    Raises ``TikTokOAuthError`` on HTTP 4xx/5xx OR a non-zero business
    ``code`` (TikTok answers HTTP 200 even on logical errors).
    """
    if resp.status_code >= 400:
        raise TikTokOAuthError(
            f"TikTok OAuth HTTP {resp.status_code}: {resp.text[:500]}"
        )
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise TikTokOAuthError(
            f"TikTok OAuth non-JSON response: {resp.text[:300]}"
        ) from exc
    if body.get("code") not in (0, None):
        raise TikTokOAuthError(
            f"TikTok OAuth code {body.get('code')}: {body.get('message')}"
        )
    return body.get("data") or {}
