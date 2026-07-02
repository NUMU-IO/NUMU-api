"""TikTok for Business OAuth route — sibling of ``oauth/meta.py``.

Two-step flow:

  * ``GET /oauth/tiktok/start?store_id=...`` — generate a CSRF state token,
    persist it to a session cookie, redirect the browser to TikTok's consent
    page.
  * ``GET /oauth/tiktok/callback?auth_code=...&state=...`` — verify state,
    exchange the auth code for a long-lived token, list the merchant's pixels,
    return a setup payload the merchant-hub picker UI renders.

**Production-readiness gate.** The route returns ``503`` until the NUMU TikTok
App credentials (``NUMU_TIKTOK_APP_ID`` / ``NUMU_TIKTOK_APP_SECRET``) are set,
so merchants get a clear "not yet available" message and fall back to the
paste-Pixel-Code + Events-API-token flow. Deploying this code is safe — it's
gated by configuration.

Token persistence is intentionally deferred to the existing settings PUT
(``PUT /stores/{id}/settings/tracking/tiktok``) which the picker submits with
the chosen ``pixel_id`` + ``api_access_token`` — so nothing is persisted for a
merchant who abandons the flow mid-picker.
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
from src.infrastructure.external_services.tiktok.oauth_client import (
    TIKTOK_OAUTH_SCOPES,
    TikTokOAuthClient,
    TikTokOAuthError,
)

logger = get_logger(__name__)
router = APIRouter()

_STATE_COOKIE = "numu_tiktok_oauth_state"
_STATE_TTL_SECONDS = 600  # 10 minutes


def _redirect_uri() -> str:
    """Reconstruct the callback URL from app config.

    TikTok requires the redirect_uri registered on the App to match; both
    ends derive it from the same config key.
    """
    base = getattr(settings, "public_api_url", None) or "http://localhost:8000"
    return f"{base.rstrip('/')}/api/v1/oauth/tiktok/callback"


def _client_or_503() -> TikTokOAuthClient:
    """Build the client, or raise 503 if the TikTok App isn't configured."""
    client = TikTokOAuthClient()
    if not client.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "TikTok OAuth is not yet available — the NUMU TikTok App "
                "credentials are not configured. Use paste-Pixel-Code mode "
                "for now via /api/v1/stores/{id}/settings/tracking/tiktok."
            ),
        )
    return client


@router.get(
    "/start",
    summary="Begin TikTok OAuth — redirect to TikTok consent screen",
    operation_id="tiktok_oauth_start",
)
async def tiktok_oauth_start(
    store_id: Annotated[UUID, Query(description="Store the connection is for")],
    request: Request,
    _user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Redirect the merchant to TikTok's consent page.

    State format: ``{random}-{store_id}`` — random is the CSRF guard, store_id
    routes the callback back to the right store. The cookie holds only the
    random half.
    """
    client = _client_or_503()
    csrf = secrets.token_urlsafe(24)
    state = f"{csrf}-{store_id}"
    url = client.authorization_url(redirect_uri=_redirect_uri(), state=state)
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
    logger.info(
        "tiktok_oauth_start_redirect",
        extra={
            "store_id": str(store_id),
            "scopes": ",".join(TIKTOK_OAUTH_SCOPES),
        },
    )
    return resp


@router.get(
    "/callback",
    summary="TikTok OAuth callback — exchange code + list pixels",
    operation_id="tiktok_oauth_callback",
)
async def tiktok_oauth_callback(
    request: Request,
    auth_code: Annotated[str | None, Query(description="Auth code from TikTok")] = None,
    code: Annotated[str | None, Query(description="Alias TikTok may use")] = None,
    state: Annotated[str | None, Query(description="CSRF + store_id state")] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
):
    """Verify the callback + return the connection-setup payload.

    TikTok appends ``auth_code`` on success (older integrations use ``code``);
    we accept either. On success the response includes the pixel list so the
    merchant-hub UI can render a picker; token storage happens on the follow-up
    settings PUT the picker submits.
    """
    if error:
        logger.warning(
            "tiktok_oauth_callback_error",
            extra={"error": error, "description": error_description},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"TikTok returned error: {error} — {error_description or ''}",
        )

    resolved_code = auth_code or code
    if not resolved_code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing auth_code or state on TikTok OAuth callback",
        )

    cookie_csrf = request.cookies.get(_STATE_COOKIE)
    state_parts = state.split("-", 1)
    if len(state_parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed state parameter",
        )
    state_csrf, state_store_id = state_parts
    if not cookie_csrf or not secrets.compare_digest(cookie_csrf, state_csrf):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSRF state mismatch — restart the connect flow",
        )

    try:
        store_uuid = UUID(state_store_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid store_id in state",
        )

    client = _client_or_503()
    try:
        tokens = await client.exchange_code_for_token(auth_code=resolved_code)
        pixels = await client.list_pixels(
            access_token=tokens.access_token,
            advertiser_ids=tokens.advertiser_ids,
        )
    except TikTokOAuthError as exc:
        logger.warning(
            "tiktok_oauth_exchange_failed",
            extra={"store_id": str(store_uuid), "error": str(exc)[:300]},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"TikTok OAuth exchange failed: {exc}",
        )

    # Token + pixels returned to the picker UI. The merchant-hub then submits
    # PUT /stores/{id}/settings/tracking/tiktok with the chosen pixel_code as
    # pixel_id and the access_token as api_access_token — that endpoint encrypts
    # + persists. Nothing is stored server-side for an abandoned flow.
    return {
        "store_id": str(store_uuid),
        "access_token": tokens.access_token,
        "advertiser_ids": tokens.advertiser_ids,
        "pixels": [
            {
                "pixel_id": p.pixel_code,  # Pixel Code = event_source_id
                "pixel_code": p.pixel_code,
                "internal_pixel_id": p.pixel_id,
                "name": p.name,
                "advertiser_id": p.advertiser_id,
            }
            for p in pixels
        ],
    }
