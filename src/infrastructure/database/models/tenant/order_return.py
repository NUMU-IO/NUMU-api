"""OrderReturn database model (Phase 3.1).

Sits next to RefundModel in the schema; the FK Refund.metadata.return_id
links the two when a return is approved and a refund is minted.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.order_return import ReturnReason, ReturnStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class OrderReturnModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Customer-initiated return request."""

    __tablename__ = "order_returns"
    __table_args__ = (
        Index("ix_order_returns_store_status", "store_id", "status"),
        Index("ix_order_returns_customer", "customer_id"),
        {"schema": "public"},
    )

    order_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    return_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    status: Mapped[ReturnStatus] = mapped_column(
        Enum(ReturnStatus, name="returnstatus", schema="public"),
        default=ReturnStatus.REQUESTED,
        nullable=False,
        index=True,
    )
    reason: Mapped[ReturnReason] = mapped_column(
        Enum(ReturnReason, name="returnreason", schema="public"),
        nullable=False,
    )

    customer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    merchant_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-line items being returned. Stored as JSONB to avoid a second
    # table for what's a tightly-coupled child record (Shopify ships
    # return_line_items as a separate table; we deliberately keep it
    # nested here because we never query against individual lines —
    # the merchant always edits the whole return as a unit).
    line_items: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)

    # Linked refund (filled when merchant approves + refund is minted).
    refund_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.refunds.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Actors
    approved_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    rejected_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    received_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    requested_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")

    extra_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
