"""Webhook event entity for audit/DLQ."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from .base import BaseEntity


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WebhookProvider(StrEnum):
    """Source of webhook event."""

    META = "meta"
    WHATSAPP = "whatsapp"


class WebhookStatus(StrEnum):
    """Processing status of webhook event."""

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DEAD = "dead"


class WebhookEvent(BaseEntity):
    """Represents a received webhook event for audit and DLQ purposes."""

    provider: WebhookProvider
    event_type: str | None = None
    external_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    signature: str | None = None
    received_at: datetime = Field(default_factory=_utc_now)
    processed_at: datetime | None = None
    status: WebhookStatus = WebhookStatus.RECEIVED
    error: str | None = None
    retry_count: int = 0

    def start_processing(self) -> None:
        """Mark event as being processed."""
        self.status = WebhookStatus.PROCESSING
        self.touch()

    def mark_processed(self) -> None:
        """Mark event as successfully processed."""
        self.status = WebhookStatus.PROCESSED
        self.processed_at = datetime.now(UTC)
        self.touch()

    def mark_failed(self, error: str) -> None:
        """Mark event as failed with error."""
        self.status = WebhookStatus.FAILED
        self.error = error
        self.retry_count += 1
        self.touch()

    def mark_dead(self) -> None:
        """Mark event as dead after max retries."""
        self.status = WebhookStatus.DEAD
        self.touch()

    def should_retry(self, max_retries: int = 3) -> bool:
        """Check if event should be retried."""
        return self.retry_count < max_retries and self.status == WebhookStatus.FAILED
