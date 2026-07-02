"""Backward-compatibility shim.

The canonical logging module now lives at :mod:`src.core.logging`. This module
re-exports its public surface so existing ``from src.config.logging_config
import ...`` call sites keep working unchanged. Prefer importing from
``src.core.logging`` in new code.
"""

from src.core.logging import (  # noqa: F401
    REDACTION_MARKER,
    SENSITIVE_LOG_KEYS,
    Log,
    LoggerAdapter,
    add_app_context,
    add_request_context,
    bind_request_context,
    clear_request_context,
    configure_logging,
    drop_none_values,
    get_logger,
    redact_sensitive_fields,
    request_id_var,
    tenant_id_var,
    user_id_var,
)

__all__ = [
    "REDACTION_MARKER",
    "SENSITIVE_LOG_KEYS",
    "Log",
    "LoggerAdapter",
    "add_app_context",
    "add_request_context",
    "bind_request_context",
    "clear_request_context",
    "configure_logging",
    "drop_none_values",
    "get_logger",
    "redact_sensitive_fields",
    "request_id_var",
    "tenant_id_var",
    "user_id_var",
]
