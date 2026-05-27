"""Meta Marketing API OAuth — separate flow from CAPI/Pixel connect.

Why this exists separately from ``oauth/meta.py``:

Many merchants connect Meta the first time via the "Conversions API
Application" path — that issues a SYSTEM_USER token whose only scope
is ``read_ads_dataset_quality`` (pixel-scoped). With that token Meta
rejects every Marketing API call (``promote-on-meta`` ad creation,
Custom Audience creates / member pushes) with code 100/200:

  > Ad account owner has NOT granted ads_management or ads_read
  > permission.

This route gives the merchant a second, additive consent flow scoped
to ``ads_management`` + ``ads_read`` + ``business_management`` only.
The resulting token is persisted under a separate
``service_credentials`` row (``ServiceName.META_MARKETING``) which the
``promote-on-meta`` and ``audiences/{id}/sync`` endpoints read from,
so the original CAPI token is left untouched.

Two-step flow:

  * ``GET /oauth/meta-marketing/start?store_id=...`` — generate CSRF
    state, redirect to Meta's consent screen with marketing scopes.
  * ``GET /oauth/meta-marketing/callback?code=...&state=...`` —
    verify state, exchange code → long-lived token, list the
    merchant's ad accounts, return the picker payload. Token
    persistence happens on the follow-up
    ``POST /stores/{id}/settings/marketing/meta/connect``.
"""

from __future__ import annotations

import html
import json
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.dependencies.auth import get_current_user_id
from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.external_services.meta.oauth_client import (
    META_MARKETING_SCOPES,
    MetaOAuthClient,
    MetaOAuthError,
)

logger = get_logger(__name__)
router = APIRouter()

_STATE_COOKIE = "numu_meta_marketing_oauth_state"
_STATE_TTL_SECONDS = 600


def _redirect_uri() -> str:
    base = getattr(settings, "public_api_url", None) or "http://localhost:8000"
    return f"{base.rstrip('/')}/api/v1/oauth/meta-marketing/callback"


def _client_or_503() -> MetaOAuthClient:
    """Build a marketing-scoped client, falling back to the main Meta app."""
    app_id = (
        getattr(settings, "meta_marketing_app_id", None)
        or getattr(settings, "meta_app_id", None)
        or ""
    )
    app_secret = (
        getattr(settings, "meta_marketing_app_secret", None)
        or getattr(settings, "meta_app_secret", None)
        or ""
    )
    if not (app_id and app_secret):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Meta Marketing OAuth is not configured — set "
                "NUMU_META_MARKETING_APP_ID + NUMU_META_MARKETING_APP_SECRET "
                "(or the base NUMU_META_APP_* vars) before enabling "
                "Promote-on-Meta / Custom Audiences."
            ),
        )
    return MetaOAuthClient(
        app_id=app_id, app_secret=app_secret, scopes=META_MARKETING_SCOPES
    )


@router.get(
    "/start",
    summary="Begin Meta Marketing OAuth — redirect to Meta consent screen",
    operation_id="meta_marketing_oauth_start",
)
async def meta_marketing_oauth_start(
    store_id: Annotated[UUID, Query(description="Store the connection is for")],
    request: Request,
    _current_user_id: Annotated[UUID, Depends(get_current_user_id)],
):
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
        "meta_marketing_oauth_start_redirect",
        extra={
            "store_id": str(store_id),
            "scopes": ",".join(META_MARKETING_SCOPES),
        },
    )
    return resp


@router.get(
    "/callback",
    summary="Meta Marketing OAuth callback — exchange code + list ad accounts",
    operation_id="meta_marketing_oauth_callback",
)
async def meta_marketing_oauth_callback(
    request: Request,
    code: Annotated[str | None, Query(description="Auth code from Meta")] = None,
    state: Annotated[str | None, Query(description="CSRF + store_id state")] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
):
    """Verify the callback + return the ad-account picker payload.

    Returns the long-lived access token alongside the list of ad
    accounts so the merchant-hub picker UI can render without another
    OAuth roundtrip. The token is persisted by a follow-up POST to
    ``/stores/{id}/settings/marketing/meta/connect`` — nothing is
    saved server-side until the merchant confirms the ad-account pick.
    """
    if error:
        logger.warning(
            "meta_marketing_oauth_callback_error",
            extra={"error": error, "description": error_description},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Meta returned error: {error} — {error_description or ''}",
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state on Meta Marketing OAuth callback",
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
        short = await client.exchange_code_for_token(
            code=code, redirect_uri=_redirect_uri()
        )
        long_lived = await client.upgrade_to_long_lived_token(
            short_lived_token=short.access_token
        )
        ad_accounts = await client.list_ad_accounts(
            access_token=long_lived.access_token
        )
        debug = await client.debug_token(access_token=long_lived.access_token)
    except MetaOAuthError as exc:
        logger.warning(
            "meta_marketing_oauth_exchange_failed",
            extra={"store_id": str(store_uuid), "error": str(exc)[:300]},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Meta OAuth exchange failed: {exc}",
        )

    granted_scopes = list(debug.get("scopes") or [])
    if "ads_management" not in granted_scopes:
        # The merchant unchecked ads_management on the consent screen.
        # Don't persist a token that can't do anything useful.
        logger.warning(
            "meta_marketing_oauth_missing_scope",
            extra={
                "store_id": str(store_uuid),
                "granted": granted_scopes,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Connection rejected — ads_management was not granted. "
                "Restart the connect flow and leave all requested "
                "permissions checked on the Meta consent screen."
            ),
        )

    # Render an HTML page that posts the result to window.opener and
    # closes the popup. The hub opens /start in a popup, listens for
    # ``message`` events of type ``meta_marketing_oauth_result``, and
    # renders the ad-account picker from the embedded payload. Token
    # crosses the postMessage boundary in plaintext, which is OK here
    # because (a) it's the merchant's own token, (b) the next hop —
    # POST /connect — re-validates it server-side before persisting.
    payload = {
        "type": "meta_marketing_oauth_result",
        "ok": True,
        "store_id": str(store_uuid),
        "access_token": long_lived.access_token,
        "expires_in": long_lived.expires_in,
        "granted_scopes": granted_scopes,
        "ad_accounts": ad_accounts,
    }
    payload_json = html.escape(json.dumps(payload), quote=False)
    body = f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Connecting Meta Ads…</title></head>
<body><p>Connection complete — you can close this window.</p>
<script>
  try {{
    const data = JSON.parse({json.dumps(payload_json)});
    if (window.opener) {{
      window.opener.postMessage(data, '*');
    }}
  }} catch (err) {{ console.error('postMessage failed', err); }}
  setTimeout(() => window.close(), 200);
</script></body></html>"""
    return HTMLResponse(content=body)
