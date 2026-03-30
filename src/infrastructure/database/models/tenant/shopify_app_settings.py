"""Shopify app settings model — per-store settings for risk scoring thresholds."""

from uuid import UUID as PyUUID

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class ShopifyAppSettingsModel(Base, UUIDMixin, TimestampMixin):
    """Per-store Shopify app settings."""

    __tablename__ = "shopify_app_settings"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        unique=True,
        nullable=False,
    )
    cod_risk_scoring_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    auto_approve_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="30",
    )
    auto_hold_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="70",
    )
    auto_cancel_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="90",
    )
    paymob_connected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    whatsapp_connected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    whatsapp_template_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    whatsapp_nudge_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
