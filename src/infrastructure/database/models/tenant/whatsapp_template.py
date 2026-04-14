"""WhatsApp message template database model."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WhatsAppTemplateModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """WhatsApp message template stored per-store."""

    __tablename__ = "whatsapp_templates"
    __table_args__ = (
        Index("idx_wa_templates_store", "store_id"),
        Index(
            "idx_wa_templates_store_name", "store_id", "name", "language", unique=True
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    meta_template_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="ar")
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UTILITY"
    )  # UTILITY, MARKETING, AUTHENTICATION
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )  # PENDING, APPROVED, REJECTED, PAUSED
    header_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    header_content: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    footer_text: Mapped[str | None] = mapped_column(String(60), nullable=True)
    buttons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_system: Mapped[bool] = mapped_column(default=False, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    store = relationship("StoreModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<WhatsAppTemplate(id={self.id}, name={self.name}, status={self.status})>"
        )
