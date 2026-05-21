"""Store entity representing a merchant store."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity
from src.core.value_objects.money import Currency


class StoreStatus(StrEnum):
    """Store status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING_APPROVAL = "pending_approval"


class Store(BaseEntity):
    """Store entity representing a merchant store.

    Stores belong to tenants and are owned by users. They contain
    products, categories, customers, and orders.
    """

    name: str
    slug: str
    owner_id: UUID
    subdomain: str | None = None  # e.g., "mystore" for mystore.numueg.app
    custom_domain: str | None = None  # e.g., "shop.mybrand.com"
    description: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None
    status: StoreStatus = StoreStatus.PENDING_APPROVAL
    default_currency: Currency = Currency.EGP
    default_language: Literal["en", "ar"] = "ar"
    contact_email: str | None = None
    contact_phone: str | None = None
    address: dict[str, Any] = Field(default_factory=dict)
    social_links: dict[str, str] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    theme_settings: dict[str, Any] = Field(
        default_factory=dict
    )  # NUMU-shop customization
    business_hours: dict[str, Any] = Field(
        default_factory=dict
    )  # Per-day open/close, theme-agnostic
    tenant_id: UUID | None = None

    @property
    def is_active(self) -> bool:
        """Check if store is active."""
        return self.status == StoreStatus.ACTIVE

    @property
    def store_url(self) -> str:
        """Get the public URL for the store."""
        if self.custom_domain:
            return f"https://{self.custom_domain}"
        if self.subdomain:
            return f"https://{self.subdomain}.numueg.app"
        return f"https://{self.slug}.numueg.app"

    @property
    def is_suspended(self) -> bool:
        """Check if store is suspended."""
        return self.status == StoreStatus.SUSPENDED

    @property
    def is_pending(self) -> bool:
        """Check if store is pending approval."""
        return self.status == StoreStatus.PENDING_APPROVAL

    def activate(self) -> None:
        """Activate the store."""
        self.status = StoreStatus.ACTIVE
        self.touch()

    def suspend(self, reason: str | None = None) -> None:
        """Suspend the store.

        Args:
            reason: Optional reason for suspension (can be stored in settings)
        """
        self.status = StoreStatus.SUSPENDED
        if reason:
            self.settings["suspension_reason"] = reason
            self.settings["suspended_at"] = datetime.utcnow().isoformat()
        self.touch()

    def deactivate(self) -> None:
        """Deactivate the store."""
        self.status = StoreStatus.INACTIVE
        self.touch()

    def approve(self) -> None:
        """Approve a pending store."""
        if self.status == StoreStatus.PENDING_APPROVAL:
            self.status = StoreStatus.ACTIVE
            self.settings["approved_at"] = datetime.utcnow().isoformat()
            self.touch()

    def update_settings(self, **kwargs: Any) -> None:
        """Update store settings.

        Args:
            **kwargs: Key-value pairs to update in settings
        """
        self.settings.update(kwargs)
        self.touch()

    def set_social_link(self, platform: str, url: str) -> None:
        """Set a social media link.

        Args:
            platform: Social platform name (e.g., 'twitter', 'instagram')
            url: URL to the social profile
        """
        self.social_links[platform] = url
        self.touch()

    def remove_social_link(self, platform: str) -> None:
        """Remove a social media link.

        Args:
            platform: Social platform name to remove
        """
        self.social_links.pop(platform, None)
        self.touch()

    def is_owned_by(self, user_id: UUID) -> bool:
        """Check if the store is owned by a specific user.

        Args:
            user_id: The user ID to check

        Returns:
            True if the user owns this store
        """
        return self.owner_id == user_id
