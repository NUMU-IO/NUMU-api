"""Channel message entity."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from .base import BaseEntity
from .channel_connection import ChannelType


class MessageDirection(StrEnum):
    """Direction of the message."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageType(StrEnum):
    """Type of message content."""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"
    TEMPLATE = "template"
    PRODUCT = "product"
    SYSTEM = "system"


class MessageStatus(StrEnum):
    """Delivery status of a message."""

    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    RECEIVED = "received"


class ChannelMessage(BaseEntity):
    """Represents a single message in a conversation thread."""

    tenant_id: UUID
    thread_id: UUID
    direction: MessageDirection
    channel: ChannelType
    external_message_id: str | None = None
    external_timestamp: datetime | None = None
    sender_external_id: str | None = None
    type: MessageType = MessageType.TEXT
    body: str | None = None
    attachment_url: str | None = None
    attachment_mime: str | None = None
    template_name: str | None = None
    template_payload: dict[str, Any] | None = None
    product_id: UUID | None = None
    status: MessageStatus = MessageStatus.RECEIVED
    error_code: str | None = None
    error_message: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    def is_inbound(self) -> bool:
        """Check if this is an inbound message."""
        return self.direction == MessageDirection.INBOUND

    def is_outbound(self) -> bool:
        """Check if this is an outbound message."""
        return self.direction == MessageDirection.OUTBOUND

    def has_attachment(self) -> bool:
        """Check if message has an attachment."""
        return self.attachment_url is not None

    def mark_sent(self) -> None:
        """Mark message as sent."""
        self.status = MessageStatus.SENT
        self.touch()

    def mark_delivered(self) -> None:
        """Mark message as delivered."""
        self.status = MessageStatus.DELIVERED
        self.touch()

    def mark_read(self) -> None:
        """Mark message as read."""
        self.status = MessageStatus.READ
        self.touch()

    def mark_failed(self, error_code: str, error_message: str) -> None:
        """Mark message as failed with error details."""
        self.status = MessageStatus.FAILED
        self.error_code = error_code
        self.error_message = error_message
        self.touch()
