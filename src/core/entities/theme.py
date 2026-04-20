"""Theme domain entities."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class ThemeType(StrEnum):
    """Theme type enumeration."""

    INTERNAL = "internal"
    EXTERNAL = "external"


class ThemeStatus(StrEnum):
    """Theme publication status."""

    DRAFT = "draft"
    PUBLISHED = "published"
    SUSPENDED = "suspended"


class Theme(BaseEntity):
    """A platform-wide theme (built-in or external).

    Themes are global resources — not scoped to a tenant.
    Stores install themes via the StoreTheme join entity.
    """

    name: str = Field(max_length=255)
    slug: str = Field(max_length=255)
    description: str | None = None
    author: str = Field(default="NUMU", max_length=255)
    type: ThemeType
    thumbnail_url: str | None = Field(default=None, max_length=500)
    is_public: bool = False
    status: ThemeStatus = ThemeStatus.DRAFT
    settings_schema: dict[str, Any] = Field(default_factory=dict)
    section_schemas: dict[str, Any] | None = None
    supported_features: dict[str, Any] | None = None  # {darkMode, rtl, ...}
    created_by: UUID | None = None

    @property
    def is_published(self) -> bool:
        """Whether the theme is available in the marketplace."""
        return self.status == ThemeStatus.PUBLISHED

    def publish(self) -> None:
        """Publish the theme to the marketplace."""
        self.status = ThemeStatus.PUBLISHED
        self.touch()

    def suspend(self) -> None:
        """Suspend a published theme (removes from marketplace)."""
        self.status = ThemeStatus.SUSPENDED
        self.touch()


class ThemeVersion(BaseEntity):
    """A versioned snapshot of a theme bundle.

    Each time a theme is built, a new ThemeVersion row is created with
    immutable bundle_url / css_url pointing to R2 objects.
    """

    theme_id: UUID
    version: str = Field(max_length=50)  # Semver: "1.0.0"
    bundle_url: str = Field(max_length=500)
    css_url: str | None = Field(default=None, max_length=500)
    manifest: dict[str, Any] = Field(default_factory=dict)  # Full theme.json snapshot
    changelog: str | None = None
    is_latest: bool = False
    size_bytes: int | None = None
    checksum: str = Field(max_length=64)  # SHA-256 of the bundle
    published_at: datetime | None = None

    def mark_latest(self) -> None:
        """Mark this version as the latest."""
        self.is_latest = True
        self.touch()


class StoreTheme(BaseEntity):
    """Records a theme installation for a store (the join entity).

    One active row per store (enforced by DB partial unique index).
    draft_customization holds unpublished edits; customization is live.
    """

    store_id: UUID
    tenant_id: UUID
    theme_id: UUID
    theme_version_id: UUID
    is_active: bool = False
    customization: dict[str, Any] = Field(default_factory=dict)
    draft_customization: dict[str, Any] = Field(default_factory=dict)
    # V3 Theme Engine columns
    customization_v3: dict[str, Any] = Field(default_factory=dict)
    draft_customization_v3: dict[str, Any] = Field(default_factory=dict)
    installed_at: datetime | None = None
    activated_at: datetime | None = None

    # Denormalized fields (populated by repositories for convenience)
    theme_slug: str | None = None
    theme_name: str | None = None
    theme_type: ThemeType | None = None
    theme_version: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    settings_schema: dict[str, Any] | None = None
    section_schemas: dict[str, Any] | None = None
    theme_thumbnail_url: str | None = None

    @property
    def has_draft_changes(self) -> bool:
        """Whether there are unsaved draft changes."""
        return bool(self.draft_customization)

    def activate(self) -> None:
        """Mark this installation as the active theme for the store."""
        from datetime import UTC

        self.is_active = True
        self.activated_at = datetime.now(UTC)
        self.touch()

    def deactivate(self) -> None:
        """Mark this installation as inactive."""
        self.is_active = False
        self.touch()

    def save_draft(self, draft: dict[str, Any]) -> None:
        """Update the draft customization."""
        self.draft_customization = draft
        self.touch()

    def publish(self) -> None:
        """Promote draft_customization → customization."""
        self.customization = dict(self.draft_customization)
        self.draft_customization = {}
        self.touch()
