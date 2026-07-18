"""COD Autopilot delivery-check conversation state. One row per eligible
shipped order; scanned by the Autopilot Celery beat tasks."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WhatsAppDeliveryCheckModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Per-order customer delivery-check lifecycle (004-cod-autopilot).

    ``outcome`` state machine (data-model.md §1):
    pending → delivered_confirmed | exception | response_exhausted
    response_exhausted → assumed_delivered | delivered_confirmed | exception
    any non-terminal → superseded (order closed by another path, FR-018)
    """

    __tablename__ = "whatsapp_delivery_checks"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    customer_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    first_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # none | received | not_yet | refused
    response: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # pending | delivered_confirmed | response_exhausted | assumed_delivered
    # | exception | superseded
    outcome: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", index=True
    )
    # refused | response_exhausted | late_contradiction
    exception_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    exception_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assumed_delivered_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    store = relationship("StoreModel", lazy="noload")
    order = relationship("OrderModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<WhatsAppDeliveryCheck(order_id={self.order_id}, "
            f"attempts={self.attempts}, outcome={self.outcome})>"
        )
