"""Funnel event database model for conversion tracking."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class FunnelEventModel(Base, UUIDMixin, TenantMixin):
    """Funnel event for conversion tracking.

    Append-only — no updated_at needed.
    Steps: page_view, product_view, add_to_cart, checkout_started,
           order_completed, order_delivered
    """

    __tablename__ = "funnel_events"
    __table_args__ = (
        Index("ix_funnel_events_store_step_created", "store_id", "step", "created_at"),
        Index("ix_funnel_events_store_created", "store_id", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Step 09 — client-provided idempotency key. Nullable so legacy
    # pre-async rows (which never had one) remain valid; uniqueness is
    # enforced by a partial UNIQUE index (WHERE event_id IS NOT NULL)
    # added in migration funnel_event_idemp_20260514.
    event_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    step: Mapped[str] = mapped_column(String(50), nullable=False)
    step_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Attribution columns — mirror orders.utm_* + campaign_id so funnel
    # reports can slice by campaign without JSONB extraction. Server
    # stamps these from the visitor's numu_attribution cookie on
    # ingest; null for legacy events and for visitors with no cookie.
    utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    campaign_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketing_campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Top-level referrer — previously stashed in step_data.referrer.
    # Promoted to a column so we can index it / filter on it without
    # JSONB extraction. Reader code falls back to step_data.referrer
    # for legacy rows that never had the column.
    referrer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Device classification (feature 002 US3). Populated at ingest by
    # the device_classifier service via ua-parser. Historical rows stay
    # NULL → surface as the "Unknown" bucket in the donut panel.
    device: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<FunnelEvent(id={self.id}, step={self.step})>"
