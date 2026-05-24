"""CampaignAutoMatchRule DB model — feature 002 US4.

Per-store auto-attribution rules for incoming funnel events. Evaluated
at ingest BEFORE the existing short_code-based campaign resolution
(see ``application/services/campaign_auto_match.py`` for the runtime).

Rows sharing a ``group_id`` form one logical multi-condition rule;
the group's ``combinator`` (AND/OR) determines how rows combine.
Priority is store-globally unique to keep precedence unambiguous —
first-match-wins across all campaigns in the store.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class CampaignAutoMatchRuleModel(Base, UUIDMixin, TenantMixin):
    __tablename__ = "campaign_auto_match_rules"
    __table_args__ = (
        Index("ix_camr_store_priority", "store_id", "priority"),
        Index("ix_camr_campaign_id", "campaign_id"),
        Index("uq_camr_store_group", "store_id", "group_id"),
        CheckConstraint("combinator IN ('AND', 'OR')", name="ck_camr_combinator"),
        CheckConstraint(
            "field IN ('utm_source', 'utm_medium', 'utm_campaign')",
            name="ck_camr_field",
        ),
        CheckConstraint(
            "operator IN ('equals', 'starts_with', 'contains')",
            name="ck_camr_operator",
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
    group_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    combinator: Mapped[str] = mapped_column(String(8), nullable=False)
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    operator: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<CampaignAutoMatchRuleModel(campaign_id={self.campaign_id}, "
            f"group_id={self.group_id}, priority={self.priority})>"
        )
