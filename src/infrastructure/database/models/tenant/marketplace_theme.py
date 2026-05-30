"""SQLAlchemy models for the theme marketplace."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import UUIDMixin


class MarketplaceThemeModel(Base, UUIDMixin):
    """A theme listing in the NUMU marketplace."""

    __tablename__ = "marketplace_themes"
    __table_args__ = {"schema": "public"}

    developer_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    price_cents: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(10), server_default="USD", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), server_default="draft", nullable=False
    )
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    preview_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    demo_store_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supported_languages: Mapped[list] = mapped_column(
        JSONB, server_default='["en","ar"]', nullable=False
    )
    supported_features: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    install_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    average_rating: Mapped[float] = mapped_column(
        Float, server_default="0.0", nullable=False
    )
    review_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    # Per-theme feature flags for the soft-migration rollout. Schema:
    #   {
    #     "catalog_visible": bool,
    #     "installable": bool,
    #     "activatable": bool,
    #     "visible_to_user_ids": [str],
    #     "visible_to_pct": int (0-100),
    #   }
    # Default ``{}`` ⇒ theme is INVISIBLE in the public catalog. Themes
    # only become visible after explicit admin flip. Protects sawsaw +
    # rabbit from auto-rolled-out themes that may not be production-ready.
    flags: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)

    # Admin-curated metadata (Session A 2026-05-27, file 04 §6). All
    # optional; populated via PATCH /marketplace/admin/themes/{id}.
    # Catalog response surfaces ``author_name`` + ``screenshots`` +
    # ``feature_tags``; detail page also surfaces ``author_url`` +
    # ``highlights``.
    author_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    author_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    screenshots: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    highlights: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    feature_tags: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
        onupdate=text("NOW()"),
        nullable=False,
    )

    versions: Mapped[list[MarketplaceThemeVersionModel]] = relationship(
        "MarketplaceThemeVersionModel", back_populates="theme", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<MarketplaceThemeModel(id={self.id}, slug={self.slug})>"


class MarketplaceThemeVersionModel(Base, UUIDMixin):
    """A versioned release of a marketplace theme."""

    __tablename__ = "marketplace_theme_versions"
    __table_args__ = (
        UniqueConstraint("theme_id", "version_string", name="uq_mtv_theme_version"),
        {"schema": "public"},
    )

    theme_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketplace_themes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_string: Mapped[str] = mapped_column(String(50), nullable=False)
    bundle_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    css_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Phase 7.3 — static BYOT templates served alongside the bundle.
    # NULL when the theme didn't declare them; storefront falls back
    # to the platform's hardcoded chrome.
    error_template_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    loading_template_url: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    settings_schema: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    section_schemas: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    presets: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), server_default="pending_build", nullable=False
    )
    build_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_zip_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )

    theme: Mapped[MarketplaceThemeModel] = relationship(
        "MarketplaceThemeModel", back_populates="versions", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<MarketplaceThemeVersionModel(id={self.id}, theme_id={self.theme_id})>"


class MarketplaceThemeInstallationModel(Base, UUIDMixin):
    """Per-store install tracking for marketplace themes.

    Separate from `store_themes` so the marketplace can record
    install/uninstall lifecycle independently of the active-theme join.
    A row with `uninstalled_at IS NULL` means the theme is currently
    installed; activation is mirrored on the StoreTheme row.
    """

    __tablename__ = "marketplace_theme_installations"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_theme_id", name="uq_mti_store_theme"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    marketplace_theme_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketplace_themes.id", ondelete="CASCADE"),
        nullable=False,
    )
    marketplace_version_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketplace_theme_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    uninstalled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<MarketplaceThemeInstallationModel(store_id={self.store_id}, "
            f"theme_id={self.marketplace_theme_id})>"
        )


class MarketplaceThemeReviewModel(Base, UUIDMixin):
    """A merchant-written review + rating for a marketplace theme.

    One row per (theme, user) pair (enforced via the unique
    constraint). Ratings are integers 1–5 (CHECK constraint at the DB
    level). The aggregates on `marketplace_themes` are kept up to date
    via the review service rather than a trigger so the recomputation
    runs in the same transaction as the user-visible mutation.
    """

    __tablename__ = "marketplace_theme_reviews"
    __table_args__ = (
        UniqueConstraint("marketplace_theme_id", "user_id", name="uq_mtr_theme_user"),
        {"schema": "public"},
    )

    marketplace_theme_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketplace_themes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_verified_purchase: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    developer_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    developer_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    helpful_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
        onupdate=text("NOW()"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<MarketplaceThemeReviewModel(id={self.id}, "
            f"theme={self.marketplace_theme_id}, rating={self.rating})>"
        )


class MarketplaceThemePurchaseModel(Base, UUIDMixin):
    """Records a paid-theme purchase.

    A row with `status="succeeded"` and `refunded_amount_cents == 0`
    grants the buyer (`user_id`) install rights for `marketplace_theme_id`
    across every store they own. Refunds (full or partial) update
    `refunded_amount_cents` rather than deleting the row so we keep an
    immutable financial trail.

    `stripe_payment_intent_id` is unique — the webhook keys idempotency
    on it. `metadata` carries pass-through Stripe metadata (e.g. the
    invoice URL) and any audit fields the application wants to stash.
    """

    __tablename__ = "marketplace_theme_purchases"
    __table_args__ = {"schema": "public"}

    user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    marketplace_theme_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketplace_themes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(10), server_default="USD", nullable=False
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    stripe_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), server_default="pending", nullable=False
    )
    refunded_amount_cents: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    refund_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Renamed from `metadata` because SQLAlchemy reserves that attribute
    # name on declarative classes. The DB column keeps the conventional
    # name via `name=`.
    purchase_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
        onupdate=text("NOW()"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<MarketplaceThemePurchaseModel(id={self.id}, "
            f"theme={self.marketplace_theme_id}, status={self.status})>"
        )
