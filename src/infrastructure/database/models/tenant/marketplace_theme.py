"""SQLAlchemy models for the theme marketplace."""

from __future__ import annotations

from uuid import UUID as PyUUID

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import UUIDMixin


class MarketplaceThemeModel(Base, UUIDMixin):
    """A theme listing in the NUMU marketplace."""

    __tablename__ = "marketplace_themes"
    __table_args__ = {"schema": "public"}

    developer_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    price_cents: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    currency: Mapped[str] = mapped_column(String(10), server_default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(50), server_default="draft", nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    preview_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    demo_store_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supported_languages: Mapped[list] = mapped_column(JSONB, server_default='["en","ar"]', nullable=False)
    supported_features: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    install_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    average_rating: Mapped[float] = mapped_column(Float, server_default="0.0", nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"), nullable=False)

    versions: Mapped[list["MarketplaceThemeVersionModel"]] = relationship(
        "MarketplaceThemeVersionModel", back_populates="theme", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<MarketplaceThemeModel(id={self.id}, slug={self.slug})>"


class MarketplaceThemeVersionModel(Base, UUIDMixin):
    """A versioned release of a marketplace theme."""

    __tablename__ = "marketplace_theme_versions"
    __table_args__ = {"schema": "public"}

    theme_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketplace_themes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_string: Mapped[str] = mapped_column(String(50), nullable=False)
    bundle_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    css_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    settings_schema: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    section_schemas: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    presets: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), server_default="pending_build", nullable=False)
    build_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"), nullable=False)

    theme: Mapped["MarketplaceThemeModel"] = relationship(
        "MarketplaceThemeModel", back_populates="versions", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<MarketplaceThemeVersionModel(id={self.id}, theme_id={self.theme_id})>"
