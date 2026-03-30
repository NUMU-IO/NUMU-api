"""Social connection entity representing a merchant's linked social media account."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.core.entities.base import BaseEntity


class SocialPlatform(StrEnum):
    """Supported social media platforms."""

    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"


class SocialConnectionStatus(StrEnum):
    """Connection status."""

    ACTIVE = "active"
    DISCONNECTED = "disconnected"


class SocialConnection(BaseEntity):
    """Represents a merchant's authorized connection to a social media platform."""

    store_id: UUID
    tenant_id: UUID | None = None
    platform: SocialPlatform
    platform_account_id: str
    handle: str
    followers: int = 0
    posts_count: int = 0
    access_token_encrypted: str | None = None
    token_expires_at: datetime | None = None
    status: SocialConnectionStatus = SocialConnectionStatus.ACTIVE
    last_synced_at: datetime | None = None

    def disconnect(self) -> None:
        """Mark this connection as disconnected."""
        self.status = SocialConnectionStatus.DISCONNECTED
        self.access_token_encrypted = None
        self.touch()

    @property
    def is_active(self) -> bool:
        return self.status == SocialConnectionStatus.ACTIVE
