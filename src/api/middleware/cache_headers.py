"""Cache headers middleware for public storefront endpoints.

Sets appropriate Cache-Control headers on public, read-only storefront
endpoints to enable browser and CDN caching. Ensures that authenticated
requests and non-storefront endpoints are never served with public caching
headers.

Public endpoints eligible for caching:
    - GET /api/v1/storefront/store-by-subdomain/{subdomain}
    - GET /api/v1/storefront/store/{store_id}/products
    - GET /api/v1/storefront/store/{store_id}/products/cursor
    - GET /api/v1/storefront/store/{store_id}/products/{slug}

Cache policy:
    - Unauthenticated GET: Cache-Control: public, max-age=60, s-maxage=120
    - Authenticated GET: Cache-Control: private, no-store
    - All other requests: untouched (SecurityHeadersMiddleware default applies)

Vary header includes Accept-Encoding and Authorization to prevent
CDNs from serving authenticated responses to unauthenticated users
(or vice versa), and to differentiate cached responses by encoding.

References:
    - RFC 7234 (HTTP Caching)
    - RFC 7232 §4.1 (304 Not Modified)
    - MDN Cache-Control: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cache-Control
"""

import logging
import re
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# URL patterns for public, cacheable storefront endpoints
_CACHEABLE_PATTERNS = [
    re.compile(r"^/api/v1/storefront/store-by-subdomain/"),
    re.compile(r"^/api/v1/storefront/store/[^/]+/products"),
]

# Cache durations (seconds)
PUBLIC_MAX_AGE = 60  # Browser cache: 1 minute
PUBLIC_S_MAXAGE = 120  # CDN/shared cache: 2 minutes
STALE_WHILE_REVALIDATE = 30  # Serve stale while fetching fresh


class CacheHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware that sets Cache-Control headers for public storefront endpoints.

    Only applies to GET requests matching known public URL patterns.
    When an Authorization header is present, the response is marked as
    private/no-store to prevent caching of authenticated content.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """Process request and set cache headers on cacheable responses."""
        response = await call_next(request)

        # Only cache GET requests
        if request.method != "GET":
            return response

        # Only cache matching storefront paths
        if not self._is_cacheable_path(request.url.path):
            return response

        # Don't override 304 Not Modified responses
        if response.status_code == 304:
            return response

        # Check for authentication — never publicly cache authenticated responses
        has_auth = "authorization" in request.headers

        if has_auth:
            response.headers["Cache-Control"] = "private, no-store"
        else:
            response.headers["Cache-Control"] = (
                f"public, max-age={PUBLIC_MAX_AGE}, "
                f"s-maxage={PUBLIC_S_MAXAGE}, "
                f"stale-while-revalidate={STALE_WHILE_REVALIDATE}"
            )

        # Vary ensures caches differentiate by encoding and auth status
        response.headers["Vary"] = "Accept-Encoding, Authorization"

        return response

    @staticmethod
    def _is_cacheable_path(path: str) -> bool:
        """Check if the URL path matches a cacheable storefront endpoint."""
        return any(pattern.search(path) for pattern in _CACHEABLE_PATTERNS)
