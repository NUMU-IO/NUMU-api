"""Structured logging middleware with request context propagation."""

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.config.logging_config import (
    bind_request_context,
    clear_request_context,
    get_logger,
)

logger = get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for structured request/response logging.

    Features:
    - Generates unique request IDs
    - Binds request context (request_id, tenant_id, user_id) to all logs
    - Logs request start and completion with timing
    - Adds X-Request-ID and X-Process-Time response headers
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with structured logging."""
        # Generate unique request ID
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        # Get tenant context if available (set by TenantMiddleware)
        tenant_id = getattr(request.state, "tenant_id", None)
        tenant_slug = getattr(request.state, "tenant_slug", None)

        # Bind request context for all subsequent logs in this request
        bind_request_context(
            request_id=request_id,
            tenant_id=str(tenant_id) if tenant_id else None,
        )

        # Create bound logger with request context
        log = logger.bind(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            tenant_slug=tenant_slug,
        )

        # Log request start
        start_time = time.time()
        log.info(
            "request_started",
            client_ip=self._get_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )

        try:
            response = await call_next(request)

            # Calculate processing time
            process_time_ms = round((time.time() - start_time) * 1000, 2)

            # Log response
            log.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=process_time_ms,
            )

            # Add response headers
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time"] = str(process_time_ms)

            return response

        except Exception as e:
            # Calculate processing time even on error
            process_time_ms = round((time.time() - start_time) * 1000, 2)

            log.exception(
                "request_failed",
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=process_time_ms,
            )
            raise

        finally:
            # Clear request context
            clear_request_context()

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request headers."""
        # Check common proxy headers
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        # Fall back to direct client
        if request.client:
            return request.client.host

        return "unknown"


# Legacy function-based middleware for backwards compatibility
async def logging_middleware(request: Request, call_next):
    """Legacy logging middleware function.

    Deprecated: Use LoggingMiddleware class instead.
    Kept for backwards compatibility during migration.
    """
    middleware = LoggingMiddleware(app=None)
    return await middleware.dispatch(request, call_next)
