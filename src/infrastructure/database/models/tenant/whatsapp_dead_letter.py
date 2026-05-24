"""Exhausted-retry record of a failed WhatsApp send. 90-day retention (FR-035a)."""

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


class WhatsAppDeadLetterModel(Base, UUIDMixin, TenantMixin):
    """A WhatsApp send that exhausted retries (or was non-retriable)."""

    __tablename__ = "whatsapp_dead_letters"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    template_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.whatsapp_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    template_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    text_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # order_created | order_paid | order_status_changed | campaign | scheduled_send | abandoned_cart | ad_hoc
    originating_context: Mapped[str] = mapped_column(String(32), nullable=False)
    originating_context_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    error_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    # retriable_exhausted | non_retriable
    error_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    final_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # not_replayed | replaying | replayed_success | replayed_failed
    replay_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_replayed"
    )
    replayed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replayed_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    replayed_send_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    store = relationship("StoreModel", lazy="noload")
    customer = relationship("CustomerModel", lazy="noload")
    template = relationship("WhatsAppTemplateModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<WhatsAppDeadLetter(id={self.id}, phone={self.phone}, "
            f"context={self.originating_context}, replay_state={self.replay_state})>"
        )
