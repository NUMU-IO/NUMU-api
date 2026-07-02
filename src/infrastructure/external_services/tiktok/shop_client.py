"""TikTok Shop (sales channel) API client.

Async wrapper around the subset of TikTok Shop's Open Platform API that the
P7 channel needs: OAuth token exchange/refresh, authorized-shop lookup, order
detail fetch, fulfillment (tracking) push, and webhook signature verification.

**External dependency status:** DORMANT until the NUMU TikTok Shop App
credentials (``NUMU_TIKTOK_SHOP_APP_KEY`` / ``NUMU_TIKTOK_SHOP_APP_SECRET``)
are set — the OAuth route returns 503 and the webhook receiver rejects unsigned
calls. Safe to deploy; gated by configuration.

TikTok Shop request signing: every Open-API call is signed with an HMAC-SHA256
of ``app_secret + path + sorted(query params as k+v) + body + app_secret``
(excluding ``sign`` / ``access_token``), hex-digested. Implemented in ``_sign``.
The auth endpoints (token get/refresh) live on a different host and are NOT
signed — only carry app_key/app_secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

# Open API (signed) + Auth host (token exchange, unsigned).
_OPEN_BASE = "https://open-api.tiktokglobalshop.com"
_AUTH_BASE = "https://auth.tiktok-shops.com"


@dataclass(frozen=True)
class TikTokShopTokens:
    """OAuth token bundle from token/get or token/refresh."""

    access_token: str
    refresh_token: str
    access_token_expire_in: int
    refresh_token_expire_in: int
    seller_name: str | None
    open_id: str | None


@dataclass(frozen=True)
class TikTokShopShop:
    """One authorized shop the token can act on."""

    shop_id: str
    shop_cipher: str
    shop_name: str | None
    region: str | None


class TikTokShopError(Exception):
    """Raised when TikTok Shop returns an error envelope or HTTP error."""


class TikTokShopClient:
    """Stateless async client for TikTok Shop's Open Platform API."""

    def __init__(
        self,
        *,
        app_key: str | None = None,
        app_secret: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_key = app_key or getattr(settings, "tiktok_shop_app_key", None) or ""
        self.app_secret = (
            app_secret or getattr(settings, "tiktok_shop_app_secret", None) or ""
        )
        self._client = client

    @property
    def is_configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    # ── OAuth (unsigned auth host) ──────────────────────────────────────

    def authorization_url(self, *, state: str) -> str:
        """Consent URL. TikTok Shop's redirect_uri is fixed on the App."""
        from urllib.parse import urlencode

        return "https://services.tiktokshop.com/open/authorize?" + urlencode({
            "app_key": self.app_key,
            "state": state,
        })

    async def exchange_code_for_token(self, *, auth_code: str) -> TikTokShopTokens:
        """Trade the authorization code for an access + refresh token."""
        return await self._token_call({
            "app_key": self.app_key,
            "app_secret": self.app_secret,
            "auth_code": auth_code,
            "grant_type": "authorized_code",
        })

    async def refresh_token(self, *, refresh_token: str) -> TikTokShopTokens:
        """Refresh an expiring access token."""
        return await self._token_call({
            "app_key": self.app_key,
            "app_secret": self.app_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })

    async def _token_call(self, params: dict[str, str]) -> TikTokShopTokens:
        endpoint = (
            "/api/v2/token/get"
            if params.get("grant_type") == "authorized_code"
            else "/api/v2/token/refresh"
        )
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.get(f"{_AUTH_BASE}{endpoint}", params=params)
            data = _parse_envelope(resp)
            return TikTokShopTokens(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", ""),
                access_token_expire_in=int(data.get("access_token_expire_in", 0)),
                refresh_token_expire_in=int(data.get("refresh_token_expire_in", 0)),
                seller_name=data.get("seller_name"),
                open_id=data.get("open_id"),
            )
        finally:
            if self._client is None:
                await client.aclose()

    # ── Open API (signed) ───────────────────────────────────────────────

    async def get_authorized_shops(self, *, access_token: str) -> list[TikTokShopShop]:
        """List the shops the token can act on (shop_id + shop_cipher)."""
        data = await self._signed_get(
            "/authorization/202309/shops", access_token=access_token, params={}
        )
        out: list[TikTokShopShop] = []
        for s in data.get("shops") or []:
            out.append(
                TikTokShopShop(
                    shop_id=str(s.get("id") or s.get("shop_id") or ""),
                    shop_cipher=str(s.get("cipher") or s.get("shop_cipher") or ""),
                    shop_name=s.get("name") or s.get("shop_name"),
                    region=s.get("region"),
                )
            )
        return out

    async def get_order_detail(
        self, *, access_token: str, shop_cipher: str, order_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch full order detail for up to 50 order ids."""
        data = await self._signed_get(
            "/order/202309/orders",
            access_token=access_token,
            params={"shop_cipher": shop_cipher, "ids": ",".join(order_ids)},
        )
        return data.get("orders") or []

    async def push_fulfillment(
        self,
        *,
        access_token: str,
        shop_cipher: str,
        order_id: str,
        tracking_number: str,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Mark an order shipped on TikTok Shop with a tracking number."""
        body = {
            "tracking_number": tracking_number,
            "shipping_provider_id": provider or "",
        }
        return await self._signed_post(
            f"/fulfillment/202309/orders/{order_id}/packages/ship",
            access_token=access_token,
            params={"shop_cipher": shop_cipher},
            body=body,
        )

    # ── Signing + transport ─────────────────────────────────────────────

    def _sign(self, path: str, params: dict[str, str], body: str) -> str:
        """HMAC-SHA256 sign per TikTok Shop's algorithm.

        Base string: ``app_secret + path + (each sorted key+value) + body +
        app_secret``, excluding ``sign`` and ``access_token``.
        """
        filtered = {
            k: v for k, v in params.items() if k not in ("sign", "access_token")
        }
        ordered = "".join(f"{k}{filtered[k]}" for k in sorted(filtered))
        base = f"{self.app_secret}{path}{ordered}{body}{self.app_secret}"
        return hmac.new(
            self.app_secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()

    def _common_params(self, extra: dict[str, str]) -> dict[str, str]:
        params = {
            "app_key": self.app_key,
            "timestamp": str(int(time.time())),
            **extra,
        }
        return params

    async def _signed_get(
        self, path: str, *, access_token: str, params: dict[str, str]
    ) -> dict[str, Any]:
        p = self._common_params(params)
        p["sign"] = self._sign(path, p, "")
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.get(
                f"{_OPEN_BASE}{path}",
                params=p,
                headers={
                    "x-tts-access-token": access_token,
                    "Content-Type": "application/json",
                },
            )
            return _parse_envelope(resp)
        finally:
            if self._client is None:
                await client.aclose()

    async def _signed_post(
        self,
        path: str,
        *,
        access_token: str,
        params: dict[str, str],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        body_str = json.dumps(body, separators=(",", ":"))
        p = self._common_params(params)
        p["sign"] = self._sign(path, p, body_str)
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.post(
                f"{_OPEN_BASE}{path}",
                params=p,
                content=body_str,
                headers={
                    "x-tts-access-token": access_token,
                    "Content-Type": "application/json",
                },
            )
            return _parse_envelope(resp)
        finally:
            if self._client is None:
                await client.aclose()

    # ── Webhook signature ───────────────────────────────────────────────

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str) -> bool:
        """Verify a TikTok Shop webhook's ``Authorization`` HMAC.

        TikTok signs ``app_key + body`` with the app_secret (HMAC-SHA256, hex).
        Constant-time compare. Returns False (never raises) on any mismatch so
        the caller can 401 cleanly.
        """
        if not self.is_configured or not signature:
            return False
        base = f"{self.app_key}{raw_body.decode('utf-8', errors='replace')}"
        expected = hmac.new(
            self.app_secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature.strip())


def _parse_envelope(resp: httpx.Response) -> dict[str, Any]:
    """Parse TikTok Shop's ``{code, message, data}`` envelope (non-zero = error)."""
    if resp.status_code >= 400:
        raise TikTokShopError(f"TikTok Shop HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise TikTokShopError(
            f"TikTok Shop non-JSON response: {resp.text[:300]}"
        ) from exc
    if body.get("code") not in (0, None):
        raise TikTokShopError(
            f"TikTok Shop code {body.get('code')}: {body.get('message')}"
        )
    return body.get("data") or {}
