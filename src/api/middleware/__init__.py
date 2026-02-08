"""API middleware module."""

from src.api.middleware.cache_headers import CacheHeadersMiddleware
from src.api.middleware.compression import CompressionMiddleware
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
from src.api.middleware.security_headers import (
    SecurityHeadersMiddleware,
    get_security_headers_middleware,
)
from src.api.middleware.sentry_middleware import SentryMiddleware
from src.api.middleware.timing import ResponseTimeMiddleware, create_timing_middleware
from src.infrastructure.tenancy.middleware import TenantMiddleware

__all__ = [
    "setup_cors",
    "error_handler_middleware",
    "setup_exception_handlers",
    "CacheHeadersMiddleware",
    "CompressionMiddleware",
    "LoggingMiddleware",
    "logging_middleware",
    "TenantMiddleware",
    "RateLimitMiddleware",
    "rate_limit_exceeded_handler",
    "RateLimitExceeded",
    "SecurityHeadersMiddleware",
    "get_security_headers_middleware",
    "SentryMiddleware",
    "ResponseTimeMiddleware",
    "create_timing_middleware",
]
