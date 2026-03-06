"""Refund database model (public schema with tenant_id discriminator)."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.refund import RefundReason, RefundStatus, RefundType
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class RefundModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Refund database model with tenant_id discriminator."""

    __tablename__ = "refunds"
    __table_args__ = {"schema": "public"}

    # References
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

    # Identification
    refund_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Type & status
    refund_type: Mapped[RefundType] = mapped_column(
        Enum(RefundType, name="refundtype", schema="public"),
        nullable=False,
    )
    status: Mapped[RefundStatus] = mapped_column(
        Enum(RefundStatus, name="refundstatus", schema="public"),
        default=RefundStatus.REQUESTED,
        nullable=False,
        index=True,
    )
    reason: Mapped[RefundReason] = mapped_column(
        Enum(RefundReason, name="refundreason", schema="public"),
        nullable=False,
    )
    reason_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Financial
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")

    # Payment provider details
    payment_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_refund_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Actors
    requested_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    approved_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    rejected_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Timestamps
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Failure tracking
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata (named refund_metadata to avoid SQLAlchemy reserved 'metadata')
    refund_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True, default=dict
    )

    # Relationships
    order = relationship("OrderModel", backref="refunds", lazy="selectin")

    def __repr__(self) -> str:
        return f"<RefundModel(id={self.id}, refund_number={self.refund_number})>"
