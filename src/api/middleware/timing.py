"""Response time tracking middleware for performance monitoring."""

import time
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.config.logging_config import get_logger

logger = get_logger(__name__)


class ResponseTimeMiddleware(BaseHTTPMiddleware):
    """Middleware to track response times and log slow requests.

    Features:
    - Adds X-Response-Time header to all responses (in milliseconds)
    - Logs requests exceeding the slow threshold as warnings
    - Uses high-precision timing with time.perf_counter()
    - Configurable slow request threshold

    This middleware is specifically designed for 3G network optimization,
    helping identify endpoints that need performance improvements.
    """

    # Default threshold for slow request warnings (milliseconds)
    DEFAULT_SLOW_THRESHOLD_MS = 500

    # Paths to exclude from slow request logging (e.g., health checks)
    EXCLUDED_PATHS = {"/health", "/api/v1/public/health", "/api/v1/public/ping"}

    def __init__(
        self,
        app,
        slow_threshold_ms: int = DEFAULT_SLOW_THRESHOLD_MS,
        log_all_requests: bool = False,
    ):
        """Initialize the middleware.

        Args:
            app: The ASGI application
            slow_threshold_ms: Threshold in milliseconds for slow request warnings
            log_all_requests: If True, log all requests (not just slow ones)
        """
        super().__init__(app)
        self.slow_threshold_ms = slow_threshold_ms
        self.log_all_requests = log_all_requests

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and track response time."""
        # Use perf_counter for high-precision timing
        start_time = time.perf_counter()

        # Process the request
        response = await call_next(request)

        # Calculate response time in milliseconds
        process_time_ms = (time.perf_counter() - start_time) * 1000

        # Add X-Response-Time header (standard header name)
        response.headers["X-Response-Time"] = f"{process_time_ms:.2f}ms"

        # Skip logging for excluded paths
        if request.url.path in self.EXCLUDED_PATHS:
            return response

        # Get request context for logging
        request_id = getattr(request.state, "request_id", "unknown")
        method = request.method
        path = request.url.path

        # Log slow requests as warnings
        if process_time_ms > self.slow_threshold_ms:
            logger.warning(
                "slow_request_detected",
                request_id=request_id,
                method=method,
                path=path,
                response_time_ms=round(process_time_ms, 2),
                threshold_ms=self.slow_threshold_ms,
                status_code=response.status_code,
                query_params=dict(request.query_params)
                if request.query_params
                else None,
                exceeded_by_ms=round(process_time_ms - self.slow_threshold_ms, 2),
            )
        elif self.log_all_requests:
            logger.debug(
                "request_timing",
                request_id=request_id,
                method=method,
                path=path,
                response_time_ms=round(process_time_ms, 2),
                status_code=response.status_code,
            )

        return response


def create_timing_middleware(
    slow_threshold_ms: int = ResponseTimeMiddleware.DEFAULT_SLOW_THRESHOLD_MS,
    log_all_requests: bool = False,
) -> type[ResponseTimeMiddleware]:
    """Factory function to create a configured ResponseTimeMiddleware.

    This allows configuring the middleware before adding it to the app.

    Usage:
        TimingMiddleware = create_timing_middleware(slow_threshold_ms=300)
        app.add_middleware(TimingMiddleware)

    Args:
        slow_threshold_ms: Threshold for slow request warnings
        log_all_requests: Whether to log all requests

    Returns:
        Configured middleware class
    """

    class ConfiguredTimingMiddleware(ResponseTimeMiddleware):
        def __init__(self, app):
            super().__init__(
                app,
                slow_threshold_ms=slow_threshold_ms,
                log_all_requests=log_all_requests,
            )

    return ConfiguredTimingMiddleware
