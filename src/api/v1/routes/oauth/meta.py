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
from src.core.logging import get_logger
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
    # (helper defined below — see `_stash_pending_oauth_token`)
    # The access token is NOT returned.
    #
    # It used to be, so the picker UI could render without a second roundtrip,
    # justified as "short-lived in the browser (~60s)". It is not short-lived:
    # `long_lived.access_token` is Meta's ~60-DAY token with whatever scopes
    # the merchant granted — `ads_management` among them. Putting that in a
    # JSON response body puts it in browser history, any logging proxy, the
    # devtools network pane and every extension on the page, for a value that
    # can spend the merchant's ad budget.
    #
    # Instead it is held server-side under a single-use handle with a short
    # TTL. The picker sends the handle back to the connect endpoint, which
    # redeems it and encrypts the token into `service_credentials` — the
    # browser never sees the secret.
    handle = await _stash_pending_oauth_token(
        store_id=store_uuid,
        access_token=long_lived.access_token,
    )

    return {
        "store_id": str(store_uuid),
        "connect_handle": handle,
        "pixels": resources.pixels,
        "pages": resources.pages,
        "catalogs": resources.catalogs,
        "business_id": resources.business_id,
    }


# ──────────────────────────────────────────────────────────────────────
# Pending-token handoff
# ──────────────────────────────────────────────────────────────────────

# How long the merchant has to finish picking a pixel before the stashed
# token expires. Long enough to read a picker, short enough that an
# abandoned flow leaves nothing usable behind.
_PENDING_TOKEN_TTL_SECONDS = 15 * 60

_PENDING_TOKEN_PREFIX = "meta_oauth_pending:"


async def _stash_pending_oauth_token(*, store_id: UUID, access_token: str) -> str:
    """Hold the OAuth token server-side and return a single-use handle.

    The callback used to return Meta's long-lived (~60 day) token straight to
    the browser so the pixel picker could render without a second roundtrip.
    That token carries whatever scopes the merchant granted — `ads_management`
    included — so it can spend their ad budget, and a JSON response body puts
    it in browser history, any logging proxy, the devtools network pane and
    every extension running on the page.

    The handle is opaque, random, scoped to one store, and redeemed exactly
    once by the connect endpoint.
    """
    import secrets as _secrets

    from src.infrastructure.cache.redis_cache import RedisCacheService

    handle = _secrets.token_urlsafe(32)
    await RedisCacheService().set(
        f"{_PENDING_TOKEN_PREFIX}{handle}",
        {"store_id": str(store_id), "access_token": access_token},
        expire=_PENDING_TOKEN_TTL_SECONDS,
    )
    return handle


async def redeem_pending_oauth_token(handle: str, store_id: UUID) -> str | None:
    """Exchange a handle for its token, once.

    Returns None when the handle is unknown, expired, already redeemed, or
    belongs to a different store — the last of which is the check that stops
    one merchant redeeming another's token by guessing a handle.
    """
    from src.infrastructure.cache.redis_cache import RedisCacheService

    if not handle:
        return None
    cache = RedisCacheService()
    key = f"{_PENDING_TOKEN_PREFIX}{handle}"
    payload = await cache.get(key)
    # Single use: burn it whether or not the store matches, so a wrong guess
    # cannot be retried against a different store.
    await cache.delete(key)
    if not isinstance(payload, dict):
        return None
    if str(payload.get("store_id")) != str(store_id):
        logger.warning(
            "meta_oauth_handle_store_mismatch",
            extra={"store_id": str(store_id)},
        )
        return None
    token = payload.get("access_token")
    return str(token) if token else None
