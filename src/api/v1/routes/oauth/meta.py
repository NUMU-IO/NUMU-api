"""Wave 3 Phase 17 — Meta Business OAuth route.

Two-step flow:

  * ``GET /oauth/meta/start?store_id=...`` — generate a CSRF state
    token, persist it under the user's session, redirect the browser
    to Meta's consent page.
  * ``GET /oauth/meta/callback?code=...&state=...`` — verify state,
    exchange code → short-lived token → long-lived token, list the
    merchant's pixels/pages/catalogs, return a setup payload the
    merchant-hub picker UI can render.

**Production-readiness gate.** The NUMU Meta App needs App Review
approval for ``ads_management`` + ``catalog_management`` scopes
(~2–6 weeks). Until that lands, this route returns ``503`` so
merchants get a clear "not yet available" message instead of an
opaque Meta error. The env vars ``NUMU_META_APP_ID`` and
``NUMU_META_APP_SECRET`` are the activation switch — set them after
App Review clears.

Picker / token persistence:

  * Single pixel + single catalog → auto-select, persist the
    encrypted System User token + pixel_id to
    ``service_credentials`` + ``store.settings.tracking.meta``.
  * Multiple of any → return the list to the UI for the merchant to
    choose, then the merchant-hub PUT /tracking/meta/connect
    finalizes the selection (a v1.1 step).
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
from src.infrastructure.external_services.meta.oauth_client import (
    META_OAUTH_SCOPES,
    MetaOAuthClient,
    MetaOAuthError,
)

logger = get_logger(__name__)
router = APIRouter()

_STATE_COOKIE = "numu_meta_oauth_state"
_STATE_TTL_SECONDS = 600  # 10 minutes


def _redirect_uri() -> str:
    """Reconstruct the callback URL from app config.

    Meta requires the redirect_uri on the callback to match what was
    sent on /start byte-for-byte, so both ends derive it from the
    same config key.
    """
    base = getattr(settings, "public_api_url", None) or "http://localhost:8000"
    return f"{base.rstrip('/')}/api/v1/oauth/meta/callback"


def _client_or_503() -> MetaOAuthClient:
    """Build the client, or raise 503 if the Meta App isn't configured."""
    client = MetaOAuthClient()
    if not client.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Meta OAuth is not yet available — the NUMU Meta App "
                "is awaiting App Review for ads_management + "
                "catalog_management scopes. Use paste-Pixel-ID mode for "
                "now via /api/v1/stores/{id}/settings/tracking/meta."
            ),
        )
    return client


@router.get(
    "/start",
    summary="Begin Meta OAuth — redirect to Meta consent screen",
    operation_id="meta_oauth_start",
)
async def meta_oauth_start(
    store_id: Annotated[UUID, Query(description="Store the connection is for")],
    request: Request,
    _user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Redirect the merchant to Meta's consent page.

    Persists a fresh CSRF state to a session cookie + carries the
    target store_id in the state itself so the callback can route
    back to the right store without an extra DB lookup.
    """
    client = _client_or_503()
    # State format: ``{random}-{store_id}`` — random is the CSRF guard,
    # store_id is the routing payload. The cookie holds only the
    # random half; both are concatenated for Meta's state param.
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
        "meta_oauth_start_redirect",
        extra={
            "store_id": str(store_id),
            "scopes": ",".join(META_OAUTH_SCOPES),
        },
    )
    return resp


@router.get(
    "/callback",
    summary="Meta OAuth callback — exchange code + list resources",
    operation_id="meta_oauth_callback",
)
async def meta_oauth_callback(
    request: Request,
    code: Annotated[str | None, Query(description="Auth code from Meta")] = None,
    state: Annotated[str | None, Query(description="CSRF + store_id state")] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
):
    """Verify the callback + return the connection-setup payload.

    On success, the response includes the pixel/page/catalog lists so
    the merchant-hub UI can render a picker. Token storage happens on
    a follow-up POST that the picker submits (a v1.1 endpoint —
    intentional separation so we don't persist credentials for a
    merchant who abandoned the flow mid-picker).
    """
    if error:
        # Meta returned an error (user denied consent, app suspended, etc.).
        logger.warning(
            "meta_oauth_callback_error",
            extra={"error": error, "description": error_description},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Meta returned error: {error} — {error_description or ''}",
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state on Meta OAuth callback",
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
        # CSRF mismatch — either cookie expired or someone replayed a URL.
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
        resources = await client.list_business_resources(
            access_token=long_lived.access_token
        )
    except MetaOAuthError as exc:
        logger.warning(
            "meta_oauth_exchange_failed",
            extra={"store_id": str(store_uuid), "error": str(exc)[:300]},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Meta OAuth exchange failed: {exc}",
        )

    # NOTE — Token persistence is intentionally deferred to a follow-up
    # POST the merchant-hub picker submits. That endpoint:
    #   1. Re-verifies the user owns the store
    #   2. Re-validates the access_token (still valid; still has scopes)
    #   3. Encrypts via SecretsManager
    #   4. Writes service_credentials + updates store.settings.tracking.meta
    # See Phase 17 v1.1: ``POST /stores/{id}/settings/tracking/meta/connect``.
    #
    # Returning the resources + token here lets the picker UI render
    # without a second OAuth roundtrip. Token is short-lived in the
    # browser (the user sees it for ~60s while picking; if they
    # abandon, nothing is persisted server-side).
    return {
        "store_id": str(store_uuid),
        "access_token": long_lived.access_token,
        "pixels": resources.pixels,
        "pages": resources.pages,
        "catalogs": resources.catalogs,
        "business_id": resources.business_id,
    }
