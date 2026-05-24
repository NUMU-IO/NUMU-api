"""CampaignActivity DB model — feature 002 US5.

Audit log of merchant-initiated campaign actions. v1 records only
``backfill_attribution``; ``type`` is extensible for future activity
kinds. Status transitions on the Celery task: ``running`` →
``completed`` | ``failed``.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class CampaignActivityModel(Base, UUIDMixin, TenantMixin):
    __tablename__ = "campaign_activities"
    __table_args__ = (
        Index(
            "ix_campaign_activities_campaign_run_at",
            "campaign_id",
            text("run_at DESC"),
        ),
        Index(
            "ix_campaign_activities_store_running",
            "store_id",
            "campaign_id",
            postgresql_where=text("status = 'running'"),
        ),
        CheckConstraint(
            "type IN ('backfill_attribution')",
            name="ck_campaign_activities_type",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_campaign_activities_status",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    campaign_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketing_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="running"
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    affected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    run_by: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<CampaignActivityModel(campaign_id={self.campaign_id}, "
            f"type={self.type}, status={self.status})>"
        )
