"""CSRF protection middleware."""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# path to exclude from CSRF protection, e.g. webhook endpoints
CSRF_EXEMPT_PATHS = (
    "/api/v1/webhooks/",
    "/api/v1/admin/auth/refresh",
    "/api/v1/admin/auth/login",
    "/api/v1/admin/auth/logout",
    "/api/v1/auth/refresh",
    "/api/v1/auth/login",
    "/api/v1/auth/2fa/complete-login",
    "/api/v1/auth/register",
    # Google sign-in is a session-establishing entry point like login/register
    # (the Google ID token IS the auth proof). A stale auth cookie from a prior
    # session made has_cookie_auth true → the CSRF gate 403'd it before a token
    # could exist, so signup/login-with-Google broke with "CSRF validation
    # failed". Exempt it for the same reason login/register are exempt.
    "/api/v1/auth/google",
    "/api/v1/auth/logout",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/verify-email-code",
    "/api/v1/auth/resend-verification",
    "/api/v1/auth/token-handoff",  # bootstrap: no CSRF cookie exists yet on redirect
    "/api/v1/staff/invitations/accept",  # guest action: user arrives from email link
    "/api/v1/storefront/store/",
    "/api/storefront/promotions/",
    "/api/v1/storefront/cart/",
    "/api/v1/storefront/checkout/",
    # NOTE: shipping/options is exempted via STOREFRONT_CSRF_EXEMPT_SUFFIXES,
    # NOT here. The old "/api/shipping/options/" entry was dead: that is the
    # storefront's Next.js proxy path — the backend only ever sees the
    # rewritten "/storefront/store/{id}/shipping/options".
    "/api/v1/public/",
    "/admin/",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# Suffixes under /api/v1/storefront/store/{store_id}/... that skip CSRF.
# The customer session cookie (customer_access_token) is domain-wide on
# .numueg.app (path "/"), so once a customer logs into their storefront
# account it rides EVERY request the storefront's Next.js proxy forwards
# server-to-server — including calls to public, no-auth endpoints. That
# trips `has_cookie_auth` below and demands an X-CSRF-Token the proxy
# never sends, 403'ing the request. The paths below are genuinely
# CSRF-irrelevant (auth entry points, or public read/compute with no
# state mutation), so we exempt them explicitly.
STOREFRONT_CSRF_EXEMPT_SUFFIXES = (
    # --- Session-establishing / auth entry points ---
    "/auth/login",
    "/auth/register",
    "/auth/refresh",
    "/auth/logout",
    "/auth/verify-email",
    "/auth/resend-verification",
    "/checkout/otp/send",
    "/checkout/otp/verify",
    # Analytics beacon: fire-and-forget POST from the storefront with no
    # CSRF header. Returning 403 on authenticated sessions caused the
    # frontend to log errors on every page view / add_to_cart.
    "/track",
    # --- Public, no-auth rate calculators (no state mutation) ---
    # POSTed from the checkout shipping step via the Next.js proxy, which
    # forwards the customer cookie but no X-CSRF-Token. Without these,
    # logged-in customers 403 at the shipping step ("CSRF validation
    # failed") while guests sail through.
    "/shipping/options",
    "/shipping/quote",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Validate CSRF token on state-changing requests.
    Uses double-submit cookie pattern:
       - A non-httpOnly `csrf_token` cookie is set via /auth/csrf-token
       - Client reads cookie and sends value in X-CSRF-Token header
       - Middleware verifies cookie == header
    """

    async def dispatch(self, request: Request, call_next):
        # Safe methods do not require CSRF token
        if request.method in SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        # Skip CSRF check for exempt paths
        if path.startswith(CSRF_EXEMPT_PATHS):
            # Storefront store routes: only exempt auth + public compute
            # endpoints (see STOREFRONT_CSRF_EXEMPT_SUFFIXES); other
            # /store/* mutations still require CSRF.
            if path.startswith("/api/v1/storefront/store/"):
                if any(path.endswith(s) for s in STOREFRONT_CSRF_EXEMPT_SUFFIXES):
                    return await call_next(request)
                # Not an exempt path — fall through to CSRF check
            else:
                return await call_next(request)

        # only validate CSRF when the request has a valid access token (i.e. is authenticated), otherwise just block to prevent token fishing
        has_cookie_auth = (
            "access_token" in request.cookies
            or "refresh_token" in request.cookies
            or "customer_access_token" in request.cookies
            or "customer_refresh_token" in request.cookies
        )
        if not has_cookie_auth:
            return await call_next(request)
        # double_submit validation
        cookie_token = request.cookies.get("csrf_token")
        header_token = request.headers.get("X-CSRF-Token")

        if not cookie_token or not header_token or cookie_token != header_token:
            logger.warning(
                f"CSRF validation failed for {request.method} {request.url.path} - "
                f"Cookie token: {cookie_token}, Header token: {header_token}"
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF validation failed"},
            )
        return await call_next(request)
