"""MetaEventLog entity — audit + idempotency record for Meta CAPI sends.

Each row corresponds to one logical event the platform attempted to send
to Meta's Conversions API. The ``UNIQUE (store_id, event_id)`` constraint
on the underlying table is the **server-side dedup primitive**: if a
duplicate row would be inserted (e.g. webhook retry, late-ack Celery
retry), the IntegrityError tells the worker to skip the outbound call.
"""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class MetaEventLog(BaseEntity):
    """Domain entity representing a Meta CAPI send attempt.

    Lifecycle:
      1. Created with ``request_payload`` filled and response columns
         null / defaulted (Phase 2 Celery task inserts the row before
         contacting Meta — UNIQUE violation = "already sent, skip").
      2. ``response_status`` / ``response_body`` / ``fbtrace_id`` /
         ``sent_at`` are filled when Meta responds.
      3. On transient failure, ``last_error`` + ``attempt_count`` are
         updated and the task retries; ``sent_at`` stays null until a
         non-retried response lands.
    """

    tenant_id: UUID
    store_id: UUID
    # Shared verbatim with the browser-side fbq() call so Meta dedupes
    # against (pixel_id, event_name, event_id). Plain string (not UUID)
    # because most non-Purchase events use synthesized IDs that aren't
    # UUIDs (e.g. "<productId>-<sessionId>" for ViewContent).
    event_id: str = Field(..., min_length=1)
    event_name: str = Field(..., min_length=1)
    event_time: datetime
    pixel_id: str = Field(..., min_length=1)
    request_payload: dict
    response_status: int | None = None
    response_body: dict | None = None
    fbtrace_id: str | None = None
    attempt_count: int = 1
    last_error: str | None = None
    sent_at: datetime | None = None

    def is_successful(self) -> bool:
        """True iff Meta acknowledged the event with a 2xx response."""
        return (
            self.sent_at is not None
            and self.response_status is not None
            and 200 <= self.response_status < 300
        )

    def mark_response(
        self,
        status: int,
        body: dict | None,
        fbtrace_id: str | None,
        sent_at: datetime,
    ) -> None:
        """Record the outcome of a CAPI HTTP call."""
        self.response_status = status
        self.response_body = body
        self.fbtrace_id = fbtrace_id
        self.sent_at = sent_at
        self.touch()

    def mark_error(self, error: str, attempt_count: int) -> None:
        """Record a transient failure prior to a retry."""
        # Truncate to a sane bound — `last_error` is debugging metadata,
        # not authoritative; matches the 500-char convention used by
        # the Phase 2 Celery task in the plan (§5.5).
        self.last_error = error[:500] if error else None
        self.attempt_count = attempt_count
        self.touch()
