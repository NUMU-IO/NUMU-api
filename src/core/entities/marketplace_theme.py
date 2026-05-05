"""Marketplace theme entities for the theme marketplace."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class MarketplaceThemeStatus(StrEnum):
    """Marketplace theme listing status."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class MarketplaceVersionStatus(StrEnum):
    """Marketplace theme version status."""

    PENDING_BUILD = "pending_build"
    BUILDING = "building"
    BUILD_FAILED = "build_failed"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class MarketplaceTheme(BaseEntity):
    """A theme listing in the NUMU marketplace."""

    developer_id: UUID
    name: str = Field(max_length=255)
    slug: str = Field(max_length=255)
    description: str | None = None
    short_description: str | None = Field(default=None, max_length=500)
    price_cents: int = 0  # 0 = free
    currency: str = "USD"
    status: MarketplaceThemeStatus = MarketplaceThemeStatus.DRAFT
    thumbnail_url: str | None = None
    preview_url: str | None = None
    demo_store_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    supported_languages: list[str] = Field(default_factory=lambda: ["en", "ar"])
    supported_features: dict[str, Any] = Field(default_factory=dict)
    install_count: int = 0
    average_rating: float = 0.0
    review_count: int = 0


class MarketplaceThemeVersion(BaseEntity):
    """A versioned release of a marketplace theme."""

    theme_id: UUID  # References MarketplaceTheme.id
    version_string: str = Field(max_length=50)  # Semver: "1.0.0"
    bundle_url: str | None = None
    css_url: str | None = None
    settings_schema: dict[str, Any] = Field(default_factory=dict)
    section_schemas: dict[str, Any] = Field(default_factory=dict)
    presets: dict[str, Any] = Field(default_factory=dict)
    release_notes: str | None = None
    status: MarketplaceVersionStatus = MarketplaceVersionStatus.PENDING_BUILD
    build_log: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    source_zip_path: str | None = None
    review_notes: str | None = None
    reviewed_by: UUID | None = None


class MarketplaceThemeInstallation(BaseEntity):
    """A per-store install record for a marketplace theme."""

    store_id: UUID
    marketplace_theme_id: UUID
    marketplace_version_id: UUID
    is_active: bool = False
    installed_at: datetime | None = None
    uninstalled_at: datetime | None = None
