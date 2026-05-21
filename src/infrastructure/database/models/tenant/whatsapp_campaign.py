"""WhatsApp broadcast campaign database models."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WhatsAppCampaignModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Broadcast campaign that sends a template message to an audience."""

    __tablename__ = "whatsapp_campaigns"
    __table_args__ = (
        Index("idx_wa_campaigns_store", "store_id"),
        Index("idx_wa_campaigns_status", "store_id", "status"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.whatsapp_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    audience_filter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    template_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", nullable=False
    )  # draft, scheduled, sending, completed, failed, cancelled
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    read_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    store = relationship("StoreModel", lazy="noload")
    template = relationship("WhatsAppTemplateModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<WhatsAppCampaign(id={self.id}, name={self.name}, status={self.status})>"
        )


class WhatsAppCampaignRecipientModel(Base, UUIDMixin):
    """Per-recipient tracking for a campaign send."""

    __tablename__ = "whatsapp_campaign_recipients"
    __table_args__ = (
        Index("idx_wa_camp_recip_campaign", "campaign_id"),
        Index("idx_wa_camp_recip_message_id", "message_id"),
        {"schema": "public"},
    )

    campaign_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.whatsapp_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )  # pending, sent, delivered, read, failed
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    campaign = relationship("WhatsAppCampaignModel", lazy="noload")

    def __repr__(self) -> str:
        return f"<CampaignRecipient(id={self.id}, phone={self.phone}, status={self.status})>"
