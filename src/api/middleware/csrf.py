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
    # --- Storefront BFF surface ---
    # The storefront is a Backend-For-Frontend: the browser talks to the
    # Next.js storefront (same-origin), which proxies to these routes
    # server-to-server. It forwards the customer's cookies but NOT the
    # backend's X-CSRF-Token — the proxy has no `csrf_token` cookie and runs
    # its own double-submit (`numu_csrf`/`x-numu-csrf`) instead. So the
    # backend double-submit can NEVER succeed here: a logged-in customer's
    # domain-wide `customer_access_token` trips has_cookie_auth below and the
    # absent header 403s the request ("CSRF validation failed"), while guests
    # (no auth cookie) sail through. Backend CSRF is thus non-functional
    # by-design for the proxied surface; SameSite=Lax cookies plus the
    # storefront's own CSRF layer are the real protection. Exempt every
    # cookie-authed proxied storefront prefix (was: /store/ gated to auth
    # suffixes only, which 403'd logged-in checkout/pay/shipping/coupons).
    "/api/v1/storefront/store/",  # checkout, pay, shipping, coupons, promotions, reviews, auth
    "/api/v1/storefront/me/",  # customer account: addresses, orders, password, saved cards
    "/api/v1/storefront/cart/",  # SDK cart aliases (NuMuProvider)
    "/api/v1/storefront/checkout/",
    "/api/storefront/promotions/",
    "/api/v1/public/",
    "/admin/",
    "/docs",
    "/redoc",
    "/openapi.json",
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
        # Skip CSRF check for exempt paths (webhooks, auth entry points, and
        # the whole BFF-proxied storefront surface — see CSRF_EXEMPT_PATHS).
        if path.startswith(CSRF_EXEMPT_PATHS):
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
