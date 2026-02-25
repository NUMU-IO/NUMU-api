"""MessageLog database model (public schema with tenant_id discriminator)."""

from uuid import UUID as PyUUID

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.message_log import MessageDirection, MessageStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class MessageLogModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Message log database model with tenant_id discriminator."""

    __tablename__ = "message_logs"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    message_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(
            MessageDirection,
            name="messagedirection",
            schema="public",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(
            MessageStatus,
            name="messagestatus",
            schema="public",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default="queued",
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    store = relationship("StoreModel", lazy="noload")

    def __repr__(self) -> str:
        return f"<MessageLogModel(id={self.id}, message_id={self.message_id})>"
