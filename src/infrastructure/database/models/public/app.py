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
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
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
    #: A private (custom) app: installs on this one store only, never listed,
    #: never reviewed, never billed by NUMU.
    private_store_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=True,
    )


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
    #: Partner Apps: ``pending_auth`` from consent until the app exchanges its
    #: code, then ``active``. NUMU Apps are always ``active``.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    #: The scopes the merchant consented to (Partner Apps).
    granted_scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
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


class AppUninstallEventModel(Base, UUIDMixin, TimestampMixin):
    """One uninstall of a Partner App. The installation row is deleted at
    uninstall, so this is the only record the partner dashboard can count."""

    __tablename__ = "app_uninstall_events"
    __table_args__ = (
        Index("ix_app_uninstall_events_app_created", "app_id", "created_at"),
        {"schema": "public"},
    )

    app_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    installed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AppRatingModel(Base, UUIDMixin, TimestampMixin):
    """One merchant's rating of an app: one per store, with at most one
    public reply from the app's partner. Hidden reviews leave the listing and
    the aggregate; ``reported_at`` puts a review in the admin queue."""

    __tablename__ = "app_ratings"
    __table_args__ = (
        UniqueConstraint("app_id", "store_id", name="uq_app_ratings_app_store"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_app_ratings_rating"),
        Index("ix_app_ratings_app_created", "app_id", "created_at"),
        Index(
            "ix_app_ratings_reported",
            "reported_at",
            postgresql_where="reported_at IS NOT NULL",
        ),
        {"schema": "public"},
    )

    app_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    report_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


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
    #: Fernet (SecretsManager): the secret signs app webhooks and "Open app"
    #: links, so it must be readable. NULL for apps created before Phase 4.
    client_secret_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    secret_key_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    secret_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AppOAuthCodeModel(Base, UUIDMixin):
    """A single-use OAuth authorization code: 10 minutes, stored hashed."""

    __tablename__ = "app_oauth_codes"
    __table_args__ = {"schema": "public"}

    installation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppAccessTokenModel(Base, UUIDMixin):
    """A ``numu_app_`` token: one store, the granted scopes, no expiry.

    Revoked on uninstall; ``revoked_at`` in the future is a rotation overlap.
    """

    __tablename__ = "app_access_tokens"
    __table_args__ = {"schema": "public"}

    installation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_installations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
