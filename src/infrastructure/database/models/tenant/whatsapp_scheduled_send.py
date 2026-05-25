"""Future-dated WhatsApp template send. Scanned by the dispatcher Celery task."""

from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    UUIDMixin,
)


class WhatsAppScheduledSendModel(Base, UUIDMixin, TenantMixin):
    """A queued, future-fire-time WhatsApp send."""

    __tablename__ = "whatsapp_scheduled_sends"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="CASCADE"),
        nullable=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    template_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.whatsapp_templates.id", ondelete="RESTRICT"),
        nullable=True,
    )
    template_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    text_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # pending | sent | cancelled | skipped | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    skip_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_order_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    store = relationship("StoreModel", lazy="noload")
    customer = relationship("CustomerModel", lazy="noload")
    template = relationship("WhatsAppTemplateModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<WhatsAppScheduledSend(id={self.id}, phone={self.phone}, "
            f"scheduled_for={self.scheduled_for}, status={self.status})>"
        )
