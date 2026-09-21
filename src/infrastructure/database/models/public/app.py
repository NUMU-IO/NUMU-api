"""App platform models — Phase 6.

Two tables:

* ``apps``           — global registry (one row per published app).
* ``app_installations`` — per-store activation, RLS-scoped via
                          ``tenant_id`` like every other tenant
                          surface in the public schema.

Both live in the ``public`` schema:
* ``apps`` is global (a Stripe-style app marketplace — same row
  visible to every tenant browsing it).
* ``app_installations`` is RLS-protected: a tenant only sees its own
  installs.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.app import AppStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class AppModel(Base, UUIDMixin, TimestampMixin):
    """A published app available for stores to install."""

    __tablename__ = "apps"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_apps_slug"),
        Index("ix_apps_status", "status"),
        {"schema": "public"},
    )

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    developer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[AppStatus] = mapped_column(
        Enum(
            AppStatus, name="appstatus", values_callable=lambda e: [m.value for m in e]
        ),
        nullable=False,
        default=AppStatus.DRAFT,
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.1.0")
    icon_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    #: Admin curation: catalog_visible (a published Partner App is hidden until
    #: set), featured, staff_pick. First-party apps (developer_id NULL) ignore
    #: catalog_visible.
    listing_flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)


class AppInstallationModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Per-store activation of an App."""

    __tablename__ = "app_installations"
    __table_args__ = (
        UniqueConstraint("store_id", "app_id", name="uq_app_installation_store_app"),
        Index("ix_app_installations_store", "store_id"),
        Index(
            "ix_app_installations_enabled",
            "store_id",
            "is_enabled",
            postgresql_where="is_enabled = true",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    app_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )


class AppUninstallModel(Base, UUIDMixin, TimestampMixin):
    """An app a store uninstalled, and when its data gets deleted.

    Uninstalling a NUMU App keeps the store's data for 30 days so a reinstall
    brings it back; reinstalling deletes this row. The daily purge task
    deletes the data of every row past ``purge_after`` (numu_apps.purge_due).
    """

    __tablename__ = "app_uninstalls"
    __table_args__ = (
        UniqueConstraint("store_id", "app_id", name="uq_app_uninstall_store_app"),
        Index("ix_app_uninstalls_purge_after", "purge_after"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    app_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    purge_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AppVersionModel(Base, UUIDMixin, TimestampMixin):
    """One uploaded version of a Partner App: a validated ``numu.app.json``.

    ``draft → submitted → in_review → approved | changes_requested | rejected
    → published → superseded``. Only a published version's listing reaches
    ``apps.manifest``, which every existing reader uses.
    """

    __tablename__ = "app_versions"
    __table_args__ = (
        UniqueConstraint("app_id", "version", name="uq_app_versions_app_version"),
        Index("ix_app_versions_status", "status"),
        {"schema": "public"},
    )

    app_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    #: {"ar": ..., "en": ...}
    release_notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    review_notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    review_checklist: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AppOAuthClientModel(Base, UUIDMixin, TimestampMixin):
    """A Partner App's OAuth credentials. The secret is stored hashed and
    shown to the partner once, at creation or rotation (Phase 4 uses it)."""

    __tablename__ = "app_oauth_clients"
    __table_args__ = {"schema": "public"}

    app_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    client_secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
