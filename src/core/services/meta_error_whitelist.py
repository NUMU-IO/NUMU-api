"""Whitelist Meta error fields surfaced to merchants (TASK-SEC-009).

Meta's error response body carries fields beyond the four we want to
expose externally (``code``, ``error_subcode``, ``message``, ``type``).
Internal-ish fields like ``fbtrace_id`` and the verbose ``error_user_*``
prompts can confuse merchants and theoretically leak diagnostic context
if a man-in-the-middle interposed.

This helper takes a raw Meta error envelope and returns a sanitized
sub-dict containing only the whitelisted keys, with non-string values
coerced to string.
"""

from typing import Any

_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "code",
    "error_subcode",
    "message",
    "type",
})


def sanitize_meta_error(error_body: Any) -> dict[str, Any] | None:
    """Extract whitelisted fields from a Meta error response body.

    Accepts either the top-level response body ``{"error": {...}, ...}``
    or the inner ``error`` object directly. Returns ``None`` if neither
    shape is recognised.
    """
    if not isinstance(error_body, dict):
        return None
    inner = error_body.get("error") if "error" in error_body else error_body
    if not isinstance(inner, dict):
        return None
    return {k: inner[k] for k in _ALLOWED_FIELDS if k in inner}
