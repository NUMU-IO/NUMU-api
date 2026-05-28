"""Classify a Meta WhatsApp API failure as retriable vs non-retriable
(FR-031 / FR-032, US6).

Pure logic — no I/O. Celery task error paths import ``classify_meta_error``
and route accordingly:

- ``retriable`` errors raise an exception that Celery's ``autoretry_for``
  catches → exponential backoff per the retry config (FR-031).
- ``non_retriable`` errors short-circuit straight to the dead-letter
  store (FR-032). Retrying these wastes Meta rate-limit budget on a
  failure that is structurally guaranteed to keep failing (e.g.,
  customer opted out at Meta-side, invalid template, malformed
  template params).

Meta's error code documentation:
  https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorClassification:
    """The verdict on a single Meta API failure."""

    retriable: bool
    # One of: "retriable_exhausted", "non_retriable"
    classification: str
    # Meta error.code (e.g. 130472, 131000) when extractable, else None
    code: str | None
    # User-facing summary
    message: str


# Meta error codes that are explicitly safe to retry — typically rate
# limiting (130429 family) or transient infra errors (500 / 599).
# Sources: Meta's published error-code table + observed retriable codes.
_RETRIABLE_META_CODES: frozenset[int] = frozenset({
    # Rate-limit family
    4,  # Application request limit reached
    17,  # User request limit reached
    80007,  # Rate limit hit
    130429,  # Rate-limit issues
    130472,  # User's number is part of an experiment
    131048,  # Spam rate limit hit
    131056,  # Pair rate limit hit
    # Transient platform errors
    1,  # Unknown / API_UNKNOWN
    2,  # Service temporarily unavailable
})

# Meta error codes that are structurally permanent. Retrying these will
# burn rate-limit budget without changing the outcome.
_NON_RETRIABLE_META_CODES: frozenset[int] = frozenset({
    # Authentication / authorization — token expired / wrong scope.
    # In BYO mode this means the merchant has to reconnect; in
    # platform-managed mode it's an ops issue. Either way: not
    # a per-message retry concern.
    190,
    102,
    # Recipient-related: opted-out, blocked, invalid number.
    131008,  # User opted out
    131009,  # Parameter values invalid
    131026,  # Message undeliverable
    131031,  # Account locked
    131036,  # Account locked
    131045,  # Template paused due to low quality
    131047,  # Re-engagement message (24h window expired)
    131051,  # Unsupported message type
    131052,  # Media download error (client-side)
    131053,  # Media upload error (client-side)
    # Template-related: doesn't exist, params don't match.
    132000,  # Template param count mismatch
    132001,  # Template doesn't exist in this language
    132005,  # Template hydrated text too long
    132007,  # Template rate exceeded (this is a per-template ban,
    # not a transient rate limit)
    132012,  # Template parameter format mismatch
    132015,  # Template paused
    132016,  # Template disabled
    132068,  # Flow / WhatsApp Business
    132069,  # Flow execution failure
})


def classify_meta_error(
    *,
    http_status: int | None,
    response_body: Any | None = None,
) -> ErrorClassification:
    """Classify a Meta API failure.

    Inputs:
        http_status: HTTP status from the response (None if e.g. network
            error before any response).
        response_body: Parsed Meta error body (``{"error": {...}}`` or
            the inner ``{...}`` object directly).

    Defaults:
        - Network / no-status errors → retriable (transient).
        - HTTP 5xx → retriable (transient platform error).
        - HTTP 429 → retriable (rate limit).
        - HTTP 4xx with a code in ``_NON_RETRIABLE_META_CODES`` → non-retriable.
        - HTTP 4xx without an extractable code → non-retriable (assume the
          merchant or platform has a structural problem; retrying won't
          help).
        - HTTP 2xx → retriable (only useful for non-HTTPStatusError paths
          like client-side parse errors).
    """
    # Extract Meta error.code if available.
    code_int: int | None = None
    code_str: str | None = None
    message = ""
    if isinstance(response_body, dict):
        inner = (
            response_body.get("error") if "error" in response_body else response_body
        )
        if isinstance(inner, dict):
            raw_code = inner.get("code")
            if isinstance(raw_code, int):
                code_int = raw_code
                code_str = str(raw_code)
            elif isinstance(raw_code, str) and raw_code.isdigit():
                code_int = int(raw_code)
                code_str = raw_code
            message = str(inner.get("message", ""))

    # Explicit Meta-code overrides win over HTTP-status defaults.
    if code_int is not None:
        if code_int in _NON_RETRIABLE_META_CODES:
            return ErrorClassification(
                retriable=False,
                classification="non_retriable",
                code=code_str,
                message=message or f"Meta error code {code_int} is non-retriable.",
            )
        if code_int in _RETRIABLE_META_CODES:
            return ErrorClassification(
                retriable=True,
                classification="retriable_exhausted",
                code=code_str,
                message=message or f"Meta error code {code_int} is retriable.",
            )

    # HTTP-status default.
    if http_status is None:
        return ErrorClassification(
            retriable=True,
            classification="retriable_exhausted",
            code=code_str,
            message=message or "Network error (no HTTP response); retriable.",
        )
    if http_status >= 500 or http_status == 429:
        return ErrorClassification(
            retriable=True,
            classification="retriable_exhausted",
            code=code_str,
            message=message or f"HTTP {http_status}; transient. Will retry.",
        )
    if 400 <= http_status < 500:
        return ErrorClassification(
            retriable=False,
            classification="non_retriable",
            code=code_str,
            message=message
            or f"HTTP {http_status}; client-side error. Will not retry.",
        )

    # Anything else (2xx, 3xx) reaching the error path is a bug surface,
    # not a real Meta failure. Default to retriable so we don't lose the
    # message; the operator can replay from DLQ if it keeps failing.
    return ErrorClassification(
        retriable=True,
        classification="retriable_exhausted",
        code=code_str,
        message=message or f"Unexpected HTTP {http_status}; treating as retriable.",
    )


class NonRetriableWhatsAppError(Exception):
    """Raised inside a Celery task when the underlying Meta error is
    non-retriable. Celery's ``autoretry_for`` tuple does NOT include
    this class so the task moves straight to the DLQ writeback path
    instead of burning retries on a structural failure.
    """

    def __init__(
        self,
        *,
        classification: str,
        code: str | None,
        message: str,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.classification = classification
        self.code = code
        self.http_status = http_status
