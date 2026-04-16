"""Channel connection entity."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from .base import BaseEntity


class ChannelType(StrEnum):
    """Supported channel types for omnichannel messaging."""

    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    WHATSAPP = "whatsapp"


class ConnectionStatus(StrEnum):
    """Status of a channel connection."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class ChannelConnection(BaseEntity):
    """Represents a connection to a messaging channel (Facebook, Instagram, WhatsApp)."""

    tenant_id: UUID
    store_id: UUID
    channel: ChannelType
    status: ConnectionStatus = ConnectionStatus.ACTIVE
    external_account_id: str | None = None
    external_account_name: str | None = None
    external_phone_number_id: str | None = None
    encrypted_credentials: bytes | None = None
    credential_key_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    webhook_subscribed_at: datetime | None = None
    token_expires_at: datetime | None = None
    last_error: str | None = None
    meta_business_id: str | None = None
    catalog_id: str | None = None
    payment_configuration_id: str | None = None

    def is_expired(self) -> bool:
        """Check if the connection token has expired."""
        if not self.token_expires_at:
            return False
        return datetime.now(UTC) >= self.token_expires_at

    def is_active(self) -> bool:
        """Check if the connection is active and not expired."""
        return self.status == ConnectionStatus.ACTIVE and not self.is_expired()
