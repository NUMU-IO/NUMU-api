"""Response time tracking middleware for performance monitoring."""

import time
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.config.logging_config import get_logger
from src.infrastructure.observability.prometheus_metrics import (
    http_request_duration_seconds,
    http_requests_in_progress,
    http_requests_total,
    status_bucket,
)

logger = get_logger(__name__)


def _route_label(request: Request) -> str:
    """Resolve a low-cardinality ``route`` label for Prometheus.

    We can't use ``scope["route"].path`` (the path template) here:
    Starlette's :class:`BaseHTTPMiddleware` shallow-copies the scope at
    the middleware boundary, so the router's mutation of
    ``scope["route"]`` (which happens deeper in the stack) isn't
    visible after ``call_next`` returns. The endpoint function
    reference *is* visible — Starlette sets ``scope["endpoint"]`` on
    the inner scope which our shallow copy points at — so we label by
    ``endpoint.__name__``.

    This collapses 10 000 distinct interpolated paths (one per UUID)
    onto one series per handler, which is exactly the cardinality
    invariant we want. Trade-off: we lose the path shape in the label
    (use the dashboard's route-template column instead).

    Falls back to ``__unmatched__`` for 404s / OPTIONS-to-no-route so
    raw URLs never leak as label values.
    """
    endpoint = request.scope.get("endpoint")
    name = getattr(endpoint, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return "__unmatched__"


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

        # In-progress gauge (decrement in finally) — kept low-cardinality
        # via the path-template label. We resolve the route *after*
        # call_next because the matched route only lands on the scope
        # once routing has run, but we want the gauge bracket to span
        # the whole request. Use a sentinel for the "in flight" window
        # and overwrite below.
        in_flight_label = "__pending__"
        http_requests_in_progress.labels(route=in_flight_label).inc()

        try:
            # Process the request
            response = await call_next(request)
        except Exception:
            # Record the abandoned in-flight before re-raising; the
            # request-level histogram is intentionally skipped here
            # because Sentry / the error-handler middleware owns
            # exception observability.
            http_requests_in_progress.labels(route=in_flight_label).dec()
            raise

        http_requests_in_progress.labels(route=in_flight_label).dec()

        # Calculate response time in milliseconds
        process_time_ms = (time.perf_counter() - start_time) * 1000

        # Add X-Response-Time header (standard header name)
        response.headers["X-Response-Time"] = f"{process_time_ms:.2f}ms"

        # Prometheus emission — use route TEMPLATE, not raw path, to
        # keep label cardinality bounded.
        route_label = _route_label(request)
        status_label = status_bucket(response.status_code)
        http_request_duration_seconds.labels(
            route=route_label, method=request.method, status=status_label
        ).observe(process_time_ms / 1000.0)
        http_requests_total.labels(
            route=route_label, method=request.method, status=status_label
        ).inc()

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
