"""Store entity representing a merchant store."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from src.core.entities.base import BaseEntity
from src.core.value_objects.money import Currency


class StoreStatus(str, Enum):
    """Store status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING_APPROVAL = "pending_approval"


class Store(BaseEntity):
    """Store entity representing a merchant store."""

    def __init__(
        self,
        name: str,
        slug: str,
        owner_id: UUID,
        description: str | None = None,
        logo_url: str | None = None,
        banner_url: str | None = None,
        status: StoreStatus = StoreStatus.PENDING_APPROVAL,
        default_currency: Currency = Currency.USD,
        contact_email: str | None = None,
        contact_phone: str | None = None,
        address: dict | None = None,
        social_links: dict | None = None,
        settings: dict | None = None,
        id: UUID | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        super().__init__(id=id, created_at=created_at, updated_at=updated_at)
        self.name = name
        self.slug = slug
        self.owner_id = owner_id
        self.description = description
        self.logo_url = logo_url
        self.banner_url = banner_url
        self.status = status
        self.default_currency = default_currency
        self.contact_email = contact_email
        self.contact_phone = contact_phone
        self.address = address or {}
        self.social_links = social_links or {}
        self.settings = settings or {}

    @property
    def is_active(self) -> bool:
        """Check if store is active."""
        return self.status == StoreStatus.ACTIVE

    def activate(self) -> None:
        """Activate the store."""
        self.status = StoreStatus.ACTIVE
        self.updated_at = datetime.utcnow()

    def suspend(self) -> None:
        """Suspend the store."""
        self.status = StoreStatus.SUSPENDED
        self.updated_at = datetime.utcnow()
