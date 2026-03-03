"""Shopify installation model — tracks installed Shopify stores."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class ShopifyInstallationModel(Base, UUIDMixin, TimestampMixin):
    """Shopify app installation record.

    NOTE: Does NOT use TenantMixin because a Shopify installation may
    be created before the corresponding Tenant record exists. The
    tenant_id is stored for future linking but has no FK constraint.
    """

    __tablename__ = "shopify_installations"
    __table_args__ = {"schema": "public"}

    # Optional link back to tenants — no FK so we can create installations
    # before a Tenant row exists.
    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    shopify_domain: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    access_token_encrypted: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    scopes: Mapped[list[str] | None] = mapped_column(
        ARRAY(String),
        nullable=True,
    )
    app_plan: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="free",
    )
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    uninstalled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
