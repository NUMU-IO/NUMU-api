"""Message thread entity."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from .base import BaseEntity
from .channel_connection import ChannelType


class ThreadStatus(StrEnum):
    """Status of a message thread."""

    OPEN = "open"
    RESOLVED = "resolved"
    SPAM = "spam"


class MessageThread(BaseEntity):
    """Represents a conversation thread with a customer on a specific channel."""

    tenant_id: UUID
    store_id: UUID
    channel: ChannelType
    channel_connection_id: UUID
    external_participant_id: str
    participant_name: str | None = None
    participant_avatar_url: str | None = None
    participant_phone_e164: str | None = None
    status: ThreadStatus = ThreadStatus.OPEN
    last_message_at: datetime
    last_message_preview: str | None = None
    unread_count: int = 0
    assigned_user_id: UUID | None = None
    metadata: dict[str, Any] = {}

    def mark_unread(self) -> None:
        """Increment unread count."""
        self.unread_count += 1
        self.touch()

    def mark_read(self) -> None:
        """Mark thread as read, resetting unread count."""
        self.unread_count = 0
        self.touch()

    def resolve(self) -> None:
        """Mark thread as resolved."""
        self.status = ThreadStatus.RESOLVED
        self.touch()

    def reopen(self) -> None:
        """Reopen a resolved thread."""
        self.status = ThreadStatus.OPEN
        self.touch()
