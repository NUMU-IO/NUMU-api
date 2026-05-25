"""WhatsApp opt-in/opt-out tracking per (store, phone). History-preserving:
re-opting after a prior opt-out creates a NEW row (FR-012).
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WhatsAppOptInModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Consent record for WhatsApp messaging."""

    __tablename__ = "whatsapp_opt_ins"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    # checkout | signup | import | api | inbound_reply
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    opted_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    opted_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # inbound_stop_keyword | merchant_revoke | customer_request_via_support | api_revoke
    opt_out_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    store = relationship("StoreModel", lazy="noload")
    customer = relationship("CustomerModel", lazy="noload")

    @property
    def is_active(self) -> bool:
        return self.opted_out_at is None

    def __repr__(self) -> str:
        state = "active" if self.is_active else "opted_out"
        return (
            f"<WhatsAppOptIn(phone={self.phone}, store={self.store_id}, state={state})>"
        )
