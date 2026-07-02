"""TikTokEventLog entity — audit + idempotency record for TikTok Events API sends.

Sibling of ``MetaEventLog``. Each row corresponds to one logical event the
platform attempted to send to TikTok's Events API v1.3. The
``UNIQUE (store_id, event_id)`` constraint on the underlying table is the
**server-side dedup primitive**: if a duplicate row would be inserted (e.g.
webhook retry, late-ack Celery retry), the IntegrityError tells the worker to
skip the outbound call.

Naming delta vs Meta: TikTok returns a ``request_id`` (its ``fbtrace_id``
analogue) and — because the Events API replies HTTP 200 with a business-level
``code`` field — we also persist ``response_code`` (0 == success).
"""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class TikTokEventLog(BaseEntity):
    """Domain entity representing a TikTok Events API send attempt.

    Lifecycle mirrors ``MetaEventLog``:
      1. Created with ``request_payload`` filled and response columns
         null / defaulted (the Celery task inserts the row before
         contacting TikTok — UNIQUE violation = "already sent, skip").
      2. ``response_status`` / ``response_code`` / ``response_body`` /
         ``request_id`` / ``sent_at`` are filled when TikTok responds.
      3. On transient failure, ``last_error`` + ``attempt_count`` are
         updated and the task retries; ``sent_at`` stays null until a
         non-retried response lands.
    """

    tenant_id: UUID
    store_id: UUID
    # Shared verbatim with the browser-side ttq.track() call so TikTok
    # dedupes against (pixel_id, event, event_id). Plain string (not
    # UUID) because most non-purchase events use synthesized IDs
    # (e.g. "<productId>-<sessionId>" for ViewContent).
    event_id: str = Field(..., min_length=1)
    event_name: str = Field(..., min_length=1)
    event_time: datetime
    pixel_id: str = Field(..., min_length=1)
    request_payload: dict
    response_status: int | None = None
    # TikTok business-level result code (0 == OK). Distinct from the
    # HTTP status because the Events API answers 200 even for logical
    # errors and carries the real outcome in the body ``code``.
    response_code: int | None = None
    response_body: dict | None = None
    request_id: str | None = None
    attempt_count: int = 1
    last_error: str | None = None
    sent_at: datetime | None = None

    def is_successful(self) -> bool:
        """True iff TikTok acknowledged the event (HTTP 2xx AND code 0)."""
        return (
            self.sent_at is not None
            and self.response_status is not None
            and 200 <= self.response_status < 300
            and self.response_code == 0
        )

    def mark_response(
        self,
        status: int,
        code: int | None,
        body: dict | None,
        request_id: str | None,
        sent_at: datetime,
    ) -> None:
        """Record the outcome of an Events API HTTP call."""
        self.response_status = status
        self.response_code = code
        self.response_body = body
        self.request_id = request_id
        self.sent_at = sent_at
        self.touch()

    def mark_error(self, error: str, attempt_count: int) -> None:
        """Record a transient failure prior to a retry."""
        self.last_error = error[:500] if error else None
        self.attempt_count = attempt_count
        self.touch()
