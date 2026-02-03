"""API middleware module."""

from src.api.middleware.cors import setup_cors
from src.api.middleware.error_handler import (
    error_handler_middleware,
    setup_exception_handlers,
)
from src.api.middleware.logging import LoggingMiddleware, logging_middleware
from src.api.middleware.rate_limit import (
    RateLimitExceeded,
    RateLimitMiddleware,
    rate_limit_exceeded_handler,
)
from src.api.middleware.sentry_middleware import SentryMiddleware
from src.infrastructure.tenancy.middleware import TenantMiddleware

__all__ = [
    "setup_cors",
    "error_handler_middleware",
    "setup_exception_handlers",
    "LoggingMiddleware",
    "logging_middleware",
    "TenantMiddleware",
    "RateLimitMiddleware",
    "rate_limit_exceeded_handler",
    "RateLimitExceeded",
    "SentryMiddleware",
]
