"""API middleware module."""

from src.api.middleware.cors import setup_cors
from src.api.middleware.error_handler import (
    error_handler_middleware,
    setup_exception_handlers,
)
from src.api.middleware.logging import logging_middleware
from src.api.middleware.tenant_middleware import TenantMiddleware

__all__ = [
    "setup_cors",
    "error_handler_middleware",
    "setup_exception_handlers",
    "logging_middleware",
    "TenantMiddleware",
]
