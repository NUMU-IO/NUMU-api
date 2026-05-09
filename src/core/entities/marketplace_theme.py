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
    # Shopify-style theme schemas are arrays of section/setting
    # definitions, NOT objects keyed by id. Type as `list | dict` so
    # we can accept both shapes — matches the SettingsSchemaShape alias
    # used elsewhere. The strict `dict[str, Any]` typing was causing
    # the build worker's MarketplaceThemeVersion validation to reject
    # legitimate theme schemas with a `dict_type` Pydantic error.
    settings_schema: list[Any] | dict[str, Any] = Field(default_factory=dict)
    section_schemas: list[Any] | dict[str, Any] = Field(default_factory=dict)
    presets: list[Any] | dict[str, Any] = Field(default_factory=dict)
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


class MarketplacePurchaseStatus(StrEnum):
    """Lifecycle of a marketplace purchase row."""

    # Created via /checkout-session, before Stripe confirms.
    PENDING = "pending"
    # Stripe webhook confirmed payment. Buyer can install across stores.
    SUCCEEDED = "succeeded"
    # Stripe webhook reported failed/canceled charge.
    FAILED = "failed"
    # Full refund processed.
    REFUNDED = "refunded"
    # Partial refund processed; buyer's install rights are still revoked
    # for new installs (we don't half-grant the right to install).
    PARTIALLY_REFUNDED = "partially_refunded"


class MarketplaceThemeReview(BaseEntity):
    """A merchant-written review for a marketplace theme.

    `is_verified_purchase` is set at insert time when the reviewer has
    a succeeded purchase row (paid theme) or an active install (free
    theme). Editing a review keeps the existing flag — verifying after
    purchase is a one-shot decision.
    """

    marketplace_theme_id: UUID
    user_id: UUID
    rating: int
    title: str | None = None
    body: str | None = None
    is_verified_purchase: bool = False
    developer_response: str | None = None
    developer_response_at: datetime | None = None
    helpful_count: int = 0


class MarketplaceThemePurchase(BaseEntity):
    """Records a single paid-theme purchase.

    A succeeded, non-refunded row grants the buyer (`user_id`) the
    right to install `marketplace_theme_id` across every store they
    own. Refunds keep the row but flip status — existing installs are
    *not* auto-uninstalled (best customer experience), but new
    install/activate calls for the same theme are blocked.
    """

    user_id: UUID
    marketplace_theme_id: UUID
    amount_cents: int
    currency: str = "USD"
    stripe_payment_intent_id: str | None = None
    stripe_charge_id: str | None = None
    status: MarketplacePurchaseStatus = MarketplacePurchaseStatus.PENDING
    refunded_amount_cents: int = 0
    refund_reason: str | None = None
    purchase_metadata: dict[str, Any] = Field(default_factory=dict)
