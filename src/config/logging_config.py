"""Structured logging configuration using structlog.

Provides JSON-formatted logs with context propagation for:
- Request ID tracking
- Tenant ID tracking
- User ID tracking
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from src.config.settings import settings

# Context variables for request-scoped data
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def add_request_context(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Add request context from context variables to log events."""
    request_id = request_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id

    tenant_id = tenant_id_var.get()
    if tenant_id:
        event_dict["tenant_id"] = tenant_id

    user_id = user_id_var.get()
    if user_id:
        event_dict["user_id"] = user_id

    return event_dict


def add_app_context(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Add application context to all log events."""
    event_dict["service"] = "numu-api"
    event_dict["environment"] = settings.environment
    event_dict["version"] = settings.app_version
    return event_dict


# Sensitive field names whose values must NEVER appear in logs (TASK-SEC-006).
# Matched case-insensitively against any key in the event dict (recursive into
# nested dicts/lists). The original value is replaced with REDACTION_MARKER.
SENSITIVE_LOG_KEYS: frozenset[str] = frozenset({
    # Authentication / authorization secrets
    "access_token",
    "refresh_token",
    "id_token",
    "bearer_token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "authorization",
    # WhatsApp / Meta BYO credentials (TASK-SEC-006 — feature: backend-030)
    "app_secret",
    "phone_number_id",
    "waba_id",
    # Payment / generic-credential fields
    "card_number",
    "cvv",
    "encrypted_credentials",
})
REDACTION_MARKER: str = "***REDACTED***"


def _redact_value(key: str, value: Any) -> Any:
    """Recurse into the value and redact wherever a sensitive key appears."""
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if key.lower() in SENSITIVE_LOG_KEYS:
        return REDACTION_MARKER
    return value


def redact_sensitive_fields(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor — redacts sensitive field values everywhere they
    appear in the event dict, including inside nested dicts/lists.

    Idempotent and cheap (no allocation when no sensitive keys present).
    """
    return {k: _redact_value(k, v) for k, v in event_dict.items()}


def configure_logging() -> None:
    """Configure structlog for the application.

    Uses JSON formatting for production and console formatting for development.
    """
    # Determine log level
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Shared processors for all logging
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        add_app_context,
        add_request_context,
        # Must come AFTER context processors and BEFORE any renderer so that
        # secrets injected via .bind() or context vars also get redacted.
        redact_sensitive_fields,
    ]

    if settings.log_format == "json":
        # JSON formatting for production
        processors: list[Processor] = [
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Console formatting for development
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging to use structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Set third-party loggers to WARNING to reduce noise
    for logger_name in ["uvicorn", "uvicorn.access", "sqlalchemy.engine"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger instance.

    Args:
        name: Logger name (usually __name__ from the calling module)

    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)


def bind_request_context(
    request_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Bind request context to context variables for log propagation.

    Call this at the start of request processing to ensure all subsequent
    logs include the request context.
    """
    if request_id:
        request_id_var.set(request_id)
    if tenant_id:
        tenant_id_var.set(tenant_id)
    if user_id:
        user_id_var.set(user_id)


def clear_request_context() -> None:
    """Clear request context from context variables.

    Call this at the end of request processing to clean up.
    """
    request_id_var.set(None)
    tenant_id_var.set(None)
    user_id_var.set(None)


class LoggerAdapter:
    """Adapter to provide a consistent logging interface.

    Wraps structlog to provide commonly used logging methods
    with automatic context binding.
    """

    def __init__(self, name: str | None = None) -> None:
        self._logger = get_logger(name)

    def bind(self, **kwargs: Any) -> "LoggerAdapter":
        """Return a new logger with bound context."""
        adapter = LoggerAdapter.__new__(LoggerAdapter)
        adapter._logger = self._logger.bind(**kwargs)
        return adapter

    def info(self, event: str, **kwargs: Any) -> None:
        """Log an info message."""
        self._logger.info(event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        """Log a warning message."""
        self._logger.warning(event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        """Log an error message."""
        self._logger.error(event, **kwargs)

    def debug(self, event: str, **kwargs: Any) -> None:
        """Log a debug message."""
        self._logger.debug(event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        """Log an exception with traceback."""
        self._logger.exception(event, **kwargs)
