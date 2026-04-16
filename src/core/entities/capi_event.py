"""CAPI (Conversions API) event entity."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .base import BaseEntity


class CapiEvent(BaseEntity):
    """Represents a CAPI event sent to Meta for ad attribution."""

    store_id: UUID
    event_name: str
    event_id: UUID
    event_time: datetime
    sent_at: datetime | None = None
    response_code: int | None = None
    response_body: dict = Field(default_factory=dict)

    def mark_sent(self) -> None:
        """Mark event as sent."""
        from datetime import UTC as DT_UTC

        self.sent_at = datetime.now(DT_UTC)
        self.touch()

    def mark_response(self, code: int, body: dict) -> None:
        """Record the API response."""
        self.response_code = code
        self.response_body = body
        self.touch()

    def is_successful(self) -> bool:
        """Check if the event was sent successfully."""
        return self.sent_at is not None and self.response_code == 200
