"""Cache headers middleware for public storefront endpoints.

Sets ``Cache-Control`` on a tightly-scoped allowlist of read-only public
storefront paths and forces ``private, no-store`` on every other route
the middleware touches when a session cookie or ``Authorization`` header
is present.

Cache policy (anonymous request on an allowlisted GET):
    Cache-Control: public, max-age=60, s-maxage=120,
                   stale-while-revalidate=30
    Vary: Cookie, Accept-Encoding

Cache policy (request carrying any session cookie / Authorization
header on an allowlisted GET):
    Cache-Control: private, no-store
    Vary: Cookie, Accept-Encoding, Authorization

Anything else is left untouched (the SecurityHeadersMiddleware default
applies).

Why this is dangerous if wrong
------------------------------
If a response that varies per session / customer is served with
``Cache-Control: public``, Cloudflare will cache one user's data and
serve it to another whose request has a different cookie but the same
URL. Cart contents, addresses, and order histories are the failure
modes that have hit other shops. That is why:

1. The allowlist is explicit, anchored regexes — no permissive
   prefix matches that could silently let a future ``/products/...``
   subpath through.
2. Any session cookie (httpOnly auth, CSRF, cart session) forces
   ``private, no-store`` even on an allowlisted path. The CSRF cookie
   is included intentionally — its presence is a strong signal of an
   authenticated session.
3. ``Vary: Cookie`` is set on BOTH branches so a downstream cache
   never reuses the no-cookie variant for a cookied request.

Routes that look cacheable but are NOT — DO NOT add to
``_CACHEABLE_PATTERNS``:

  /api/v1/storefront/me/*                   — customer-specific
  /api/v1/storefront/store/*/checkout       — may carry personal info
  /api/v1/storefront/store/*/customers      — admin / merchant scope
  /api/v1/storefront/store/*/coupons        — varies by eligibility
  /api/v1/storefront/store/*/promotions/*   — varies by visitor fp
  /api/v1/storefront/store/*/search*        — query-space too large
  /api/v1/stores/*                          — merchant-authenticated
  /api/v1/admin/*                           — admin-authenticated
  /api/v1/auth/*                            — sensitive (CSRF, login)

References:
    - RFC 7234 (HTTP Caching)
    - RFC 7232 §4.1 (304 Not Modified)
"""

import logging
import re
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Cache durations (seconds)
PUBLIC_MAX_AGE = 60  # Browser cache: 1 minute
PUBLIC_S_MAXAGE = 120  # CDN/shared cache: 2 minutes
STALE_WHILE_REVALIDATE = 30  # Serve stale while fetching fresh

# Cookies whose presence means the response may vary per session /
# customer / merchant / admin. If ANY of these is in the request, the
# middleware downgrades the response to ``private, no-store`` even on
# an allowlisted path.
#
# Keep this list in sync with every ``Response.set_cookie(...)`` /
# ``response.headers["set-cookie"]`` in the codebase. The test in
# tests/api/middleware/test_cache_headers_audit.py pins the minimum
# set so a forgotten new cookie can't silently slip through.
_SESSION_COOKIE_NAMES: tuple[str, ...] = (
    # Merchant / store-owner auth (set by src/api/utils/cookies.py)
    "access_token",
    "refresh_token",
    # Storefront customer auth
    "customer_access_token",
    "customer_refresh_token",
    # Super-admin auth
    "admin_access_token",
    "admin_refresh_token",
    # CSRF double-submit cookie — non-httpOnly but presence
    # indicates an active session
    "csrf_token",
    # Guest cart session (set by storefront cart owner middleware)
    "numu_cart_session",
    # Visitor fingerprint cookie used for promotion / coupon
    # eligibility — included so coincidental caching on adjacent
    # routes can't fall back to a per-visitor variant
    "numu_visitor",
    # Starlette SessionMiddleware default cookie name (admin panel)
    "session",
)

# Allowlist of GET paths the middleware will set Cache-Control on.
# EVERY pattern MUST be anchored (^ ... $/?$). A permissive prefix
# match (e.g. r"^/api/v1/storefront/store/[^/]+/products") would
# silently let /products/internal-admin or /products/123/foo through
# and is forbidden.
#
# To add a new pattern: classify the route per the matrix in Plan
# §3 (A through F), confirm it is purely store-scoped public data,
# update EXPECTED_CACHEABLE in the audit tests, and ship the two
# together. The snapshot test fails until you do.
_CACHEABLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ── Class A — public, store-scoped ──────────────────────────
    re.compile(r"^/api/v1/storefront/store-by-subdomain/[^/]+/?$"),
    re.compile(r"^/api/v1/storefront/store-by-domain/[^/]+/?$"),
    re.compile(r"^/api/v1/storefront/store/[0-9a-f-]+/products/?$"),
    re.compile(r"^/api/v1/storefront/store/[0-9a-f-]+/products/cursor/?$"),
    re.compile(r"^/api/v1/storefront/store/[0-9a-f-]+/products/[^/]+/?$"),
    re.compile(r"^/api/v1/storefront/store/[0-9a-f-]+/categories(?:/[^/]+)?/?$"),
    # Theme resolution route is mounted at /storefront/theme/{store_id},
    # NOT /storefront/store/{store_id}/theme. The plan template had it
    # reversed; the regex below matches the actual route registered in
    # src/api/v1/routes/__init__.py.
    re.compile(r"^/api/v1/storefront/theme/[0-9a-f-]+/?$"),
    # ── Class B — public, platform-scoped ──────────────────────
    re.compile(r"^/api/v1/public/landing-config/?$"),
)


def _has_session_cookie(request: Request) -> bool:
    cookies = request.cookies
    return any(name in cookies for name in _SESSION_COOKIE_NAMES)


class CacheHeadersMiddleware(BaseHTTPMiddleware):
    """Set ``Cache-Control`` on a tight allowlist of public GET routes.

    Cookie-bearing requests are always downgraded to
    ``private, no-store`` so a cached response can never leak across
    sessions.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        response = await call_next(request)

        if request.method != "GET":
            return response
        if not self._is_cacheable_path(request.url.path):
            return response
        if response.status_code == 304:
            return response

        has_session = _has_session_cookie(request)
        has_auth_header = "authorization" in request.headers

        if has_session or has_auth_header:
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Cookie, Accept-Encoding, Authorization"
        else:
            response.headers["Cache-Control"] = (
                f"public, max-age={PUBLIC_MAX_AGE}, "
                f"s-maxage={PUBLIC_S_MAXAGE}, "
                f"stale-while-revalidate={STALE_WHILE_REVALIDATE}"
            )
            # CRITICAL: Vary on Cookie even on the public branch. If a
            # client adds a session cookie partway through, a shared
            # cache must not reuse the no-cookie variant.
            response.headers["Vary"] = "Cookie, Accept-Encoding"

        return response

    @staticmethod
    def _is_cacheable_path(path: str) -> bool:
        return any(pattern.match(path) for pattern in _CACHEABLE_PATTERNS)
