"""Rate limiting middleware using Redis."""

import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.config import settings

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after} seconds.")


class RateLimiter:
    """Simple in-memory rate limiter.

    For production, consider using Redis-based rate limiting with slowapi
    or a similar library for distributed rate limiting.
    """

    def __init__(self):
        self._requests: dict[str, list[float]] = {}

    def _get_key(self, request: Request) -> str:
        """Generate a unique key for the request."""
        # Use client IP and path for rate limiting
        client_ip = self._get_client_ip(request)
        return f"{client_ip}:{request.url.path}"

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP address from request."""
        # Check for forwarded headers (behind proxy)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fall back to direct client
        if request.client:
            return request.client.host

        return "unknown"

    def is_allowed(
        self,
        request: Request,
        max_requests: int,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        """Check if request is allowed under rate limit.

        Args:
            request: The incoming request
            max_requests: Maximum requests allowed in window
            window_seconds: Time window in seconds

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
        """
        import time

        key = self._get_key(request)
        current_time = time.time()
        window_start = current_time - window_seconds

        # Get existing requests for this key
        if key not in self._requests:
            self._requests[key] = []

        # Filter out old requests outside the window
        self._requests[key] = [
            req_time for req_time in self._requests[key] if req_time > window_start
        ]

        # Check if limit exceeded
        if len(self._requests[key]) >= max_requests:
            # Calculate retry after
            oldest_request = min(self._requests[key])
            retry_after = int(oldest_request + window_seconds - current_time) + 1
            return False, max(retry_after, 1)

        # Add current request
        self._requests[key].append(current_time)
        return True, 0

    def cleanup(self) -> None:
        """Clean up old entries to prevent memory leak."""
        import time

        current_time = time.time()
        # Clean entries older than 2 minutes
        cutoff = current_time - 120

        keys_to_delete = []
        for key, timestamps in self._requests.items():
            self._requests[key] = [t for t in timestamps if t > cutoff]
            if not self._requests[key]:
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del self._requests[key]


# Global rate limiter instance
rate_limiter = RateLimiter()


# Auth endpoints that need stricter rate limiting
AUTH_ENDPOINTS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/public/auth/login",
    "/api/v1/public/auth/register",
    "/api/v1/public/auth/refresh",
    "/api/v1/storefront/auth/login",
    "/api/v1/storefront/auth/register",
    "/api/v1/storefront/customers/login",
    "/api/v1/storefront/customers/register",
}

# Endpoints to skip rate limiting
SKIP_RATE_LIMIT = {
    "/",
    "/health",
    "/api/v1/health",
    "/api/v1/public/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limiting on API endpoints."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with rate limiting."""
        # Skip if rate limiting is disabled
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path

        # Skip rate limiting for certain endpoints
        if path in SKIP_RATE_LIMIT:
            return await call_next(request)

        # Determine rate limit based on endpoint
        if path in AUTH_ENDPOINTS:
            max_requests = settings.rate_limit_auth_requests_per_minute
        else:
            max_requests = settings.rate_limit_requests_per_minute

        # Check rate limit
        is_allowed, retry_after = rate_limiter.is_allowed(
            request, max_requests=max_requests, window_seconds=60
        )

        if not is_allowed:
            logger.warning(
                f"Rate limit exceeded for {rate_limiter._get_client_ip(request)} "
                f"on {path}"
            )
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": "Too many requests. Please slow down.",
                    "code": "RATE_LIMIT_EXCEEDED",
                    "details": {"retry_after": retry_after},
                },
                headers={"Retry-After": str(retry_after)},
            )

        # Add rate limit headers to response
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, max_requests - len(rate_limiter._requests.get(
                rate_limiter._get_key(request), []
            )))
        )

        return response


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle rate limit exceeded exceptions."""
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "error": "Too many requests. Please slow down.",
            "code": "RATE_LIMIT_EXCEEDED",
            "details": {"retry_after": exc.retry_after},
        },
        headers={"Retry-After": str(exc.retry_after)},
    )
