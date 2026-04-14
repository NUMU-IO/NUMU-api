"""WhatsApp conversation database model for chat inbox."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WhatsAppConversationModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Represents a WhatsApp conversation thread with a customer."""

    __tablename__ = "whatsapp_conversations"
    __table_args__ = (
        Index("idx_wa_conv_store_last_msg", "store_id", "last_message_at"),
        Index("idx_wa_conv_store_phone", "store_id", "customer_phone", unique=True),
        Index("idx_wa_conv_store_status", "store_id", "status"),
        {"schema": "public"},
    )

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
    customer_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_profile_pic_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_message_preview: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_message_direction: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # "inbound" | "outbound"
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )  # active, archived, spam
    assigned_to: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    window_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    store = relationship("StoreModel", lazy="noload")

    def __repr__(self) -> str:
        return f"<WhatsAppConversation(id={self.id}, phone={self.customer_phone})>"
