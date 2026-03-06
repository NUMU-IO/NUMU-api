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
    "/api/v1/auth/refresh",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/logout",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/verify-email-code",
    "/api/v1/auth/resend-verification",
    "/api/v1/storefront/store/",
    "/api/v1/storefront/cart/",
    "/api/v1/storefront/checkout/",
    "/api/v1/public/",
    "/admin/",
    "/docs",
    "/redoc",
    "/openapi.json",
)

STOREFRONT_AUTH_EXEMPT_SUFFIXES = (
    "/auth/login",
    "/auth/register",
    "/auth/refresh",
    "/auth/logout",
    "/auth/verify-email",
    "/auth/resend-verification",
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
            # Storefront store routes: only exempt auth endpoints
            if path.startswith("/api/v1/storefront/store/"):
                if any(path.endswith(s) for s in STOREFRONT_AUTH_EXEMPT_SUFFIXES):
                    return await call_next(request)
                # Not an auth path — fall through to CSRF check
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
