"""MessageLog entity for WhatsApp / messaging audit trail."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class MessageDirection(StrEnum):
    """Direction of the message relative to the platform."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(StrEnum):
    """Lifecycle status of a message."""

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class MessageLog(BaseEntity):
    """Domain entity representing a single message log entry.

    Each record tracks a WhatsApp (or other channel) message sent or received
    by a store, including delivery status and optional template information.
    """

    tenant_id: UUID | None = None
    store_id: UUID
    phone: str = Field(..., min_length=1, max_length=20)
    metadata: dict | None = None
    message_id: str = Field(..., min_length=1, max_length=255)
    direction: MessageDirection
    template_name: str | None = Field(None, max_length=255)
    content: str | None = None
    status: MessageStatus = MessageStatus.QUEUED
    error_code: str | None = Field(None, max_length=100)
