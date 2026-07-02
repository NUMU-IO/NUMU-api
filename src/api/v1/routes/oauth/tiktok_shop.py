"""TikTok Shop (sales channel) OAuth route — sibling of ``oauth/tiktok.py``.

  * ``GET /oauth/tiktok-shop/start?store_id=...`` — CSRF state cookie → redirect
    to TikTok Shop's authorization page.
  * ``GET /oauth/tiktok-shop/callback?code=...&state=...`` — verify state,
    exchange the code for access + refresh tokens, list the authorized shops,
    return a setup payload the hub renders. The follow-up connect PUT persists.

**503 until ``NUMU_TIKTOK_SHOP_APP_KEY`` / ``NUMU_TIKTOK_SHOP_APP_SECRET`` are
set** — safe to deploy; gated by configuration.
"""

from __future__ import annotations

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from src.api.dependencies.auth import get_current_user_id
from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.external_services.tiktok.shop_client import (
    TikTokShopClient,
    TikTokShopError,
)

logger = get_logger(__name__)
router = APIRouter()

_STATE_COOKIE = "numu_tiktok_shop_oauth_state"
_STATE_TTL_SECONDS = 600


def _client_or_503() -> TikTokShopClient:
    client = TikTokShopClient()
    if not client.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "TikTok Shop OAuth is not yet available — the NUMU TikTok Shop "
                "App credentials are not configured."
            ),
        )
    return client


@router.get(
    "/start",
    summary="Begin TikTok Shop OAuth — redirect to authorization page",
    operation_id="tiktok_shop_oauth_start",
)
async def tiktok_shop_oauth_start(
    store_id: Annotated[UUID, Query(description="Store the connection is for")],
    request: Request,
    _user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    client = _client_or_503()
    csrf = secrets.token_urlsafe(24)
    state = f"{csrf}-{store_id}"
    url = client.authorization_url(state=state)
    resp = RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    resp.set_cookie(
        _STATE_COOKIE,
        csrf,
        max_age=_STATE_TTL_SECONDS,
        httponly=True,
        secure=getattr(settings, "session_cookie_secure", False),
        samesite="lax",
        path=request.url.path.rsplit("/", 1)[0] + "/",
    )
    logger.info("tiktok_shop_oauth_start_redirect", extra={"store_id": str(store_id)})
    return resp


@router.get(
    "/callback",
    summary="TikTok Shop OAuth callback — exchange code + list shops",
    operation_id="tiktok_shop_oauth_callback",
)
async def tiktok_shop_oauth_callback(
    request: Request,
    code: Annotated[str | None, Query(description="Auth code")] = None,
    state: Annotated[str | None, Query(description="CSRF + store_id state")] = None,
):
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state on TikTok Shop OAuth callback",
        )

    cookie_csrf = request.cookies.get(_STATE_COOKIE)
    parts = state.split("-", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed state"
        )
    state_csrf, state_store_id = parts
    if not cookie_csrf or not secrets.compare_digest(cookie_csrf, state_csrf):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSRF state mismatch — restart the connect flow",
        )
    try:
        store_uuid = UUID(state_store_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid store_id"
        )

    client = _client_or_503()
    try:
        tokens = await client.exchange_code_for_token(auth_code=code)
        shops = await client.get_authorized_shops(access_token=tokens.access_token)
    except TikTokShopError as exc:
        logger.warning(
            "tiktok_shop_oauth_exchange_failed",
            extra={"store_id": str(store_uuid), "error": str(exc)[:300]},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"TikTok Shop OAuth exchange failed: {exc}",
        )

    # Return the token bundle + shops to the hub picker; the follow-up connect
    # PUT persists (encrypted credential + settings). Nothing stored for an
    # abandoned flow.
    return {
        "store_id": str(store_uuid),
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "seller_name": tokens.seller_name,
        "shops": [
            {
                "shop_id": s.shop_id,
                "shop_cipher": s.shop_cipher,
                "shop_name": s.shop_name,
                "region": s.region,
            }
            for s in shops
        ],
    }
