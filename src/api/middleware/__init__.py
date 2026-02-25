"""API middleware module."""

from src.api.middleware.cache_headers import CacheHeadersMiddleware
from src.api.middleware.compression import CompressionMiddleware
from src.api.middleware.cors import setup_cors
from src.api.middleware.csrf import CSRFMiddleware
from src.api.middleware.docs_auth import DocsAuthMiddleware
from src.api.middleware.error_handler import (
    error_handler_middleware,
    setup_exception_handlers,
)
from src.api.middleware.logging import LoggingMiddleware, logging_middleware
from src.api.middleware.rate_limit import RateLimitMiddleware
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
    "DocsAuthMiddleware",
    "LoggingMiddleware",
    "logging_middleware",
    "TenantMiddleware",
    "RateLimitMiddleware",
    "SecurityHeadersMiddleware",
    "get_security_headers_middleware",
    "SentryMiddleware",
    "ResponseTimeMiddleware",
    "create_timing_middleware",
    "CSRFMiddleware",
]
