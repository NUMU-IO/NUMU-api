"""NUMU canonical logging module.

One readable, consistent logging surface for the whole codebase. Wraps structlog
so every call site gets structured JSON logs (shipped to CloudWatch), automatic
request/tenant context, secret redaction, and two first-class extras:

- ``log.insight(...)`` — business/analytics events (order_placed, trust_decision,
  payment_failed). Tagged ``kind="insight"`` so they're queryable/dashboardable
  separately from ops logs.
- ``log.alert(...)`` — logs AND dispatches to an alert webhook (Slack/n8n/HTTP)
  for real-time alerting. Destination is env-configured; unconfigured = log-only.

Usage
-----
    from src.core.logging import get_logger

    log = get_logger(__name__)

    log.info("cache_warmed", keys=42)                 # ops log
    log.warning("payment_retry", attempt=2)
    log.error("gateway_timeout", gateway="paymob")
    log.exception("unexpected")                        # inside an `except`

    log.insight("order_placed", order_id=oid, amount_cents=12000, currency="EGP")
    log.alert("fraud_spike", level="error", store_id=sid, score=0.98)

    # Bind context that decorates every subsequent line:
    olog = log.bind(order_id=oid, store_id=sid)
    olog.info("order_confirmed")

Only explicit ``log.*`` calls emit logs — there is no automatic per-request line.
Request context (request_id/tenant) is bound by the logging middleware so those
explicit lines stay correlatable.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from src.config.settings import settings

# =============================================================================
# Request-scoped context (bound once by middleware, merged into every log line)
# =============================================================================
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def bind_request_context(
    request_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Bind request context so all subsequent logs in this request carry it."""
    if request_id:
        request_id_var.set(request_id)
    if tenant_id:
        tenant_id_var.set(tenant_id)
    if user_id:
        user_id_var.set(user_id)


def clear_request_context() -> None:
    """Clear request context at the end of request processing."""
    request_id_var.set(None)
    tenant_id_var.set(None)
    user_id_var.set(None)


# =============================================================================
# structlog processors
# =============================================================================
def add_request_context(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Merge request/tenant/user context vars into the event."""
    if (request_id := request_id_var.get()) is not None:
        event_dict.setdefault("request_id", request_id)
    if (tenant_id := tenant_id_var.get()) is not None:
        event_dict.setdefault("tenant_id", tenant_id)
    if (user_id := user_id_var.get()) is not None:
        event_dict.setdefault("user_id", user_id)
    return event_dict


def add_app_context(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Add minimal application context.

    ``version`` is intentionally omitted (constant per process, redundant on
    every line); the CloudWatch log group already encodes the service.
    """
    event_dict["service"] = "numu-api"
    event_dict["environment"] = settings.environment
    return event_dict


def drop_none_values(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Strip keys whose value is None to keep log lines lean.

    Bound context (e.g. user_id/store_id/tenant_slug) is frequently None; those
    keys add bytes to every CloudWatch event for no signal. Runs last, just
    before the renderer.
    """
    return {k: v for k, v in event_dict.items() if v is not None}


# Sensitive field names whose values must NEVER appear in logs (TASK-SEC-006).
# Matched case-insensitively against any key (recursive into nested dicts/lists).
SENSITIVE_LOG_KEYS: frozenset[str] = frozenset({
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
    "app_secret",
    "phone_number_id",
    "waba_id",
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
    """Redact sensitive field values anywhere they appear in the event dict."""
    return {k: _redact_value(k, v) for k, v in event_dict.items()}


def configure_logging() -> None:
    """Configure logging for the whole application. Call once at startup.

    Both structlog loggers AND plain stdlib ``logging.getLogger`` loggers are
    routed through one structlog ``ProcessorFormatter``, so every line — wherever
    it originates — is consistent JSON (or dev console) with request/tenant
    context, secret redaction, and null-stripping. Legacy stdlib ``%s`` calls are
    interpolated via ``PositionalArgumentsFormatter`` so they don't leak format
    strings.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    # Build the event dict. Shared by structlog loggers and, via
    # foreign_pre_chain below, by stdlib loggers too.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        # Interpolate stdlib-style %-args, e.g. logger.info("x=%s", v).
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        add_app_context,
        add_request_context,
        # AFTER context, BEFORE render: redact secrets from .bind()/context too.
        redact_sensitive_fields,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            # Hand the event dict to the stdlib ProcessorFormatter for rendering.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # One formatter renders records from BOTH structlog and plain stdlib loggers.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            # Runs last: strip None-valued keys after all context is merged.
            drop_none_values,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Pin noisy third-party loggers to WARNING regardless of the root level.
    for logger_name in (
        "uvicorn",
        "uvicorn.access",
        "sqlalchemy.engine",
        "httpx",
        "httpcore",
        "boto3",
        "botocore",
        "urllib3",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


# =============================================================================
# Alert webhook dispatch (env-configured; unconfigured = log-only)
# =============================================================================
def _dispatch_alert(payload: dict[str, Any]) -> None:
    """Hand an alert off to the webhook, out of band. Never raises.

    Delivery runs in a Celery task (retries, off the request path). The task is
    referenced by NAME via ``send_task`` so this module never imports Celery at
    import time (no circular deps) and works from sync + async call sites. If no
    webhook URL is configured, this is a no-op — ``alert()`` still logs.
    """
    if not getattr(settings, "log_alert_webhook_url", ""):
        return
    try:
        from src.infrastructure.messaging.celery_app import celery_app

        celery_app.send_task(
            "tasks.dispatch_log_alert",
            kwargs={"payload": payload},
            queue="messaging",
        )
    except Exception:  # noqa: BLE001 — alerting must never break the caller
        logging.getLogger(__name__).debug(
            "alert_dispatch_enqueue_failed", exc_info=True
        )


# =============================================================================
# The readable logger
# =============================================================================
_LEVELS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})


class Log:
    """Readable, consistent logger used across the codebase.

    A thin wrapper over a structlog ``BoundLogger``. Standard levels
    (``debug/info/warning/error/critical/exception``) behave exactly like
    structlog; ``.bind()`` returns a new ``Log`` so context chains cleanly;
    ``.insight()`` and ``.alert()`` add the NUMU-specific channels. Any other
    attribute is delegated to the underlying structlog logger for full
    compatibility.
    """

    __slots__ = ("_log",)

    def __init__(self, name: str | None = None) -> None:
        self._log = structlog.get_logger(name)

    @classmethod
    def _wrap(cls, bound: Any) -> Log:
        obj = cls.__new__(cls)
        obj._log = bound
        return obj

    # -- context -----------------------------------------------------------
    def bind(self, **kwargs: Any) -> Log:
        """Return a new logger with the given context bound to every line."""
        return Log._wrap(self._log.bind(**kwargs))

    def unbind(self, *keys: str) -> Log:
        """Return a new logger with the given context keys removed."""
        return Log._wrap(self._log.unbind(*keys))

    # -- standard levels ---------------------------------------------------
    def debug(self, event: str, **kwargs: Any) -> None:
        self._log.debug(event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log.info(event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log.warning(event, **kwargs)

    warn = warning

    def error(self, event: str, **kwargs: Any) -> None:
        self._log.error(event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self._log.critical(event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        """Log at error level WITH the current exception traceback."""
        self._log.exception(event, **kwargs)

    # -- NUMU channels -----------------------------------------------------
    def insight(self, event: str, **kwargs: Any) -> None:
        """Emit a business/analytics insight, tagged ``kind="insight"``.

        Use for meaningful domain events you'll want to query or dashboard:
        ``log.insight("order_placed", order_id=..., amount_cents=..., ...)``.
        """
        self._log.info(event, kind="insight", **kwargs)

    def alert(self, event: str, *, level: str = "error", **kwargs: Any) -> None:
        """Log the event AND dispatch it to the alert webhook (if configured).

        Use for things a human should see in real time:
        ``log.alert("fraud_spike", store_id=..., score=...)``. Falls back to a
        plain log when no webhook URL is set.
        """
        emit = getattr(self._log, level, self._log.error)
        emit(event, kind="alert", **kwargs)
        _dispatch_alert({"event": event, "level": level, **kwargs})

    # -- compatibility fallback -------------------------------------------
    def __getattr__(self, item: str) -> Any:
        # __slots__ means _log is a real slot; guard against recursion if it is
        # not yet assigned, otherwise delegate everything else to structlog.
        if item == "_log":
            raise AttributeError(item)
        return getattr(self._log, item)


def get_logger(name: str | None = None) -> Log:
    """Return the canonical NUMU logger.

    Args:
        name: usually ``__name__`` from the calling module.
    """
    return Log(name)


# Backwards-compatible alias — historical code referenced ``LoggerAdapter``.
LoggerAdapter = Log


__all__ = [
    "Log",
    "LoggerAdapter",
    "get_logger",
    "configure_logging",
    "bind_request_context",
    "clear_request_context",
    "add_request_context",
    "add_app_context",
    "drop_none_values",
    "redact_sensitive_fields",
    "SENSITIVE_LOG_KEYS",
    "REDACTION_MARKER",
    "request_id_var",
    "tenant_id_var",
    "user_id_var",
]
