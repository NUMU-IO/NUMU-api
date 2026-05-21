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

from typing import Any

from sqlalchemy import (
    Boolean,
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
