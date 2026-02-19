"""Security headers middleware.

This middleware adds essential security headers to all HTTP responses
to protect against common web vulnerabilities like:
- Clickjacking (X-Frame-Options)
- MIME-type sniffing (X-Content-Type-Options)
- Protocol downgrade attacks (Strict-Transport-Security)
- Cross-site scripting (Content-Security-Policy, X-XSS-Protection)
- Information leakage (Referrer-Policy)
- Feature abuse (Permissions-Policy)
"""

import logging
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.config import settings

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware that adds security headers to all responses.

    Security headers are a critical defense-in-depth mechanism that
    instructs browsers to enable various security features.
    """

    # Default CSP directives for API responses (no browser-rendered content).
    # Restrictive by default; docs pages get a relaxed policy below.
    DEFAULT_CSP = (
        "default-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    # Permissions Policy - restrict powerful browser features
    DEFAULT_PERMISSIONS_POLICY = (
        "accelerometer=(), "
        "camera=(), "
        "geolocation=(), "
        "gyroscope=(), "
        "magnetometer=(), "
        "microphone=(), "
        "payment=(), "
        "usb=()"
    )

    def __init__(
        self,
        app: ASGIApp,
        csp: str | None = None,
        permissions_policy: str | None = None,
        hsts_max_age: int = 31536000,  # 1 year in seconds
        include_hsts: bool = True,
    ) -> None:
        """Initialize the security headers middleware.

        Args:
            app: The ASGI application
            csp: Custom Content-Security-Policy header value
            permissions_policy: Custom Permissions-Policy header value
            hsts_max_age: Max-age for HSTS header (default: 1 year)
            include_hsts: Whether to include HSTS header (disable for dev)
        """
        super().__init__(app)
        self.csp = csp or self.DEFAULT_CSP
        self.permissions_policy = permissions_policy or self.DEFAULT_PERMISSIONS_POLICY
        self.hsts_max_age = hsts_max_age
        self.include_hsts = include_hsts

    # Relaxed CSP for Swagger/ReDoc documentation pages (debug only).
    # Permits CDN scripts/styles needed by the Swagger UI and ReDoc renderers.
    DOCS_CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "font-src 'self' https: data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """Process the request and add security headers to response.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware/route handler

        Returns:
            Response with security headers added
        """
        # Process the request
        response = await call_next(request)

        # Use relaxed CSP for API documentation pages in debug mode
        is_docs_path = request.url.path in self.DOCS_PATHS
        # /uploads/ serves static assets that must be loadable from storefront/dashboard origins
        is_uploads_path = request.url.path.startswith("/uploads/")
        self._add_security_headers(
            response,
            use_docs_csp=is_docs_path,
            is_public_asset=is_uploads_path,
        )

        return response

    def _add_security_headers(
        self,
        response: Response,
        *,
        use_docs_csp: bool = False,
        is_public_asset: bool = False,
    ) -> None:
        """Add all security headers to the response.

        Args:
            response: The HTTP response to modify
            use_docs_csp: Whether to use the relaxed docs CSP policy
            is_public_asset: Whether this is a public asset (e.g. /uploads/)
        """
        # X-Frame-Options: Prevent clickjacking by disallowing iframe embedding
        # DENY = never allow framing, SAMEORIGIN = only same origin can frame
        response.headers["X-Frame-Options"] = "DENY"

        # X-Content-Type-Options: Prevent MIME-type sniffing
        # Forces browser to use declared Content-Type
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Strict-Transport-Security (HSTS): Force HTTPS connections
        # max-age: How long to remember HTTPS preference
        # includeSubDomains: Apply to all subdomains
        if self.include_hsts:
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self.hsts_max_age}; includeSubDomains"
            )

        # Content-Security-Policy: Control resources the browser can load
        # Mitigates XSS, data injection, and other attacks
        csp = self.DOCS_CSP if use_docs_csp else self.csp
        response.headers["Content-Security-Policy"] = csp

        # X-XSS-Protection: Legacy XSS filter (deprecated but still useful)
        # 1; mode=block = Enable filter and block page if attack detected
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer-Policy: Control how much referrer info is sent
        # strict-origin-when-cross-origin = Full URL for same-origin,
        # only origin for cross-origin HTTPS, nothing for HTTP downgrade
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions-Policy: Control browser feature access
        # Restricts access to sensitive APIs like camera, microphone, etc.
        response.headers["Permissions-Policy"] = self.permissions_policy

        # Additional security headers
        # X-Permitted-Cross-Domain-Policies: Control Flash/PDF cross-domain access
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        # Cross-Origin-Embedder-Policy: Control embedding resources
        # require-corp = Only allow explicitly marked resources
        # Note: Can break third-party integrations, use carefully
        # response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"

        # Cross-Origin-Opener-Policy: Isolate browsing context
        # same-origin = Prevent cross-origin window access
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"

        # Cross-Origin-Resource-Policy: Control resource loading
        # same-origin = Only allow same-origin requests
        # Public assets (e.g. /uploads/) need cross-origin so dashboard/storefront can load them
        response.headers["Cross-Origin-Resource-Policy"] = (
            "cross-origin" if is_public_asset else "same-origin"
        )

        # X-DNS-Prefetch-Control: Prevent DNS prefetching of external links
        response.headers["X-DNS-Prefetch-Control"] = "off"

        # Strip Server header to reduce technology fingerprinting
        if "server" in response.headers:
            del response.headers["server"]

        # Cache-Control for security-sensitive responses
        # Prevent caching of authenticated content
        # Note: You may want to conditionally apply this based on the route
        if response.status_code != 304:  # Don't override Not Modified responses
            # For API responses, prevent caching by default
            if "Cache-Control" not in response.headers:
                response.headers["Cache-Control"] = "no-store, max-age=0"


def get_security_headers_middleware(
    csp: str | None = None,
    include_hsts: bool | None = None,
) -> type[SecurityHeadersMiddleware]:
    """Factory function to create configured SecurityHeadersMiddleware.

    Args:
        csp: Custom Content-Security-Policy
        include_hsts: Whether to include HSTS (defaults to non-debug mode)

    Returns:
        Configured SecurityHeadersMiddleware class
    """
    # Default to including HSTS in production (non-debug mode)
    if include_hsts is None:
        include_hsts = not settings.debug

    class ConfiguredSecurityHeadersMiddleware(SecurityHeadersMiddleware):
        def __init__(self, app: ASGIApp):
            super().__init__(
                app,
                csp=csp,
                include_hsts=include_hsts,
            )

    return ConfiguredSecurityHeadersMiddleware
