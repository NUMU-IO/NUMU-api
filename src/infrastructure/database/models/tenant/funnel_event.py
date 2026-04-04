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
    session_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    step: Mapped[str] = mapped_column(String(50), nullable=False)
    step_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<FunnelEvent(id={self.id}, step={self.step})>"
