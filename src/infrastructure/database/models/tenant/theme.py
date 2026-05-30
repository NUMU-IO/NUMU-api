"""SQLAlchemy models for the theme engine.

Includes:
- ThemeModel      — global theme catalog (public.themes)
- ThemeVersionModel — versioned bundles (public.theme_versions)
- StoreThemeModel — per-store installation (public.store_themes, tenant-scoped)
- ThemeAssetModel — per-version static assets (public.theme_assets)
"""

from __future__ import annotations

from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ThemeModel(Base, UUIDMixin, TimestampMixin):
    """Global theme catalog — not tenant-scoped.

    A ThemeModel represents a theme that is available on the NUMU platform.
    Stores install themes via StoreThemeModel.
    """

    __tablename__ = "themes"
    __table_args__ = {"schema": "public"}

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="NUMU"
    )
    type: Mapped[str] = mapped_column(
        Enum("internal", "external", name="themetype", schema="public"),
        nullable=False,
    )
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        Enum("draft", "published", "suspended", name="themestatus", schema="public"),
        nullable=False,
        server_default="draft",
    )
    settings_schema: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    section_schemas: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    supported_features: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # {darkMode: bool, rtl: bool, ...}
    created_by: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Relationships
    versions: Mapped[list[ThemeVersionModel]] = relationship(
        "ThemeVersionModel", back_populates="theme", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<ThemeModel(id={self.id}, slug={self.slug})>"


class ThemeVersionModel(Base, UUIDMixin, TimestampMixin):
    """Versioned snapshot of a built theme bundle.

    Immutable once created — bundle_url points to a content-hashed R2 object.
    """

    __tablename__ = "theme_versions"
    __table_args__ = (
        UniqueConstraint("theme_id", "version", name="uq_theme_version"),
        {"schema": "public"},
    )

    theme_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.themes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "1.0.0"
    bundle_url: Mapped[str] = mapped_column(String(500), nullable=False)
    css_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_latest: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    theme: Mapped[ThemeModel] = relationship(
        "ThemeModel", back_populates="versions", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<ThemeVersionModel(theme_id={self.theme_id}, version={self.version})>"


class StoreThemeModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Records a theme installation for a store.

    Enforces at most ONE active theme per store via partial unique index.
    Holds both published customization and draft (unpublished) customization.
    """

    __tablename__ = "store_themes"
    __table_args__ = (
        # Enforce at most one active installation per store at the DB level
        Index(
            "ix_store_themes_active",
            "store_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    theme_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.themes.id"),
        nullable=False,
        index=True,
    )
    theme_version_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.theme_versions.id"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", index=True
    )
    customization: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    draft_customization: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # V3 Theme Engine columns (additive — Alembic migration 20260420_add_theme_v3_columns)
    customization_v3: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    draft_customization_v3: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    installed_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships (selectin for eager loading where needed)
    theme: Mapped[ThemeModel] = relationship("ThemeModel", lazy="selectin")
    theme_version: Mapped[ThemeVersionModel] = relationship(
        "ThemeVersionModel", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<StoreThemeModel(store_id={self.store_id}, "
            f"theme_id={self.theme_id}, is_active={self.is_active})>"
        )


class StoreThemeSnapshotModel(Base, UUIDMixin, TenantMixin):
    """Snapshot of a store's theme customization, taken BEFORE a destructive
    write (theme switch, dev-mode reconnect, marketplace activation).

    The pipeline that overwrites ``store_themes.customization_v3`` first
    writes a row here so the merchant can revert with one click. Critical
    for sawsaw + rabbit during the Phase 1 V3 rollout — a bad theme
    activation no longer means lost customization.

    Schema mirrors `alembic/versions/20260525_020000_add_store_theme_snapshots.py`.
    """

    __tablename__ = "store_theme_snapshots"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    theme_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.themes.id", ondelete="SET NULL"),
        nullable=True,
    )
    theme_version_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.theme_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    customization: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    customization_v3: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    reason: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    restored_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<StoreThemeSnapshotModel(store_id={self.store_id}, "
            f"theme_id={self.theme_id}, reason={self.reason!r})>"
        )


class ThemeAssetModel(Base, UUIDMixin, TimestampMixin):
    """Static asset files for a specific theme version.

    Populated during Phase 3 (ZIP upload pipeline).
    Created now so the schema is stable.
    """

    __tablename__ = "theme_assets"
    __table_args__ = {"schema": "public"}

    theme_version_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.theme_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(
        String(500), nullable=False
    )  # Relative path within the theme
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)  # R2 key
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256

    def __repr__(self) -> str:
        return f"<ThemeAssetModel(version_id={self.theme_version_id}, path={self.file_path})>"
