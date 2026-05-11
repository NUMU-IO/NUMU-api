"""Marketing campaign DB model — Phase 8.6."""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.marketing_campaign import CampaignChannel, CampaignStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class MarketingCampaignModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "marketing_campaigns"
    __table_args__ = (
        Index("ix_campaigns_store_status", "store_id", "status"),
        # Hot path for the Celery sweep: find SCHEDULED campaigns
        # whose scheduled_at <= now(). Partial index keeps it small.
        Index(
            "ix_campaigns_scheduled",
            "scheduled_at",
            postgresql_where="status = 'scheduled' AND scheduled_at IS NOT NULL",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[CampaignChannel] = mapped_column(
        Enum(
            CampaignChannel,
            name="campaignchannel",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(
            CampaignStatus,
            name="campaignstatus",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CampaignStatus.DRAFT,
    )
    template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    inline_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inline_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    segment_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    audience_filter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
