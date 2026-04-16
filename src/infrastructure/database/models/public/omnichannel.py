"""Channel connection database model."""

from datetime import datetime
from uuid import UUID as UUID_T

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ChannelConnectionModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Database model for channel connections (Facebook, Instagram, WhatsApp)."""

    __tablename__ = "channel_connections"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    external_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_phone_number_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_credentials: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    credential_key_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    webhook_subscribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_business_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    catalog_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_configuration_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ChannelConnectionModel(id={self.id}, channel={self.channel}, store_id={self.store_id})>"


class MessageThreadModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Database model for message threads (conversations)."""

    __tablename__ = "message_threads"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    channel_connection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.channel_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_participant_id: Mapped[str] = mapped_column(Text, nullable=False)
    participant_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    participant_avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    participant_phone_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    unread_count: Mapped[int] = mapped_column(nullable=False, default=0)
    assigned_user_id: Mapped[UUID_T | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    thread_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"<MessageThreadModel(id={self.id}, channel={self.channel}, store_id={self.store_id})>"


class ChannelMessageModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Database model for channel messages."""

    __tablename__ = "channel_messages"
    __table_args__ = {"schema": "public"}

    thread_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.message_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sender_external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_mime: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    product_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"<ChannelMessageModel(id={self.id}, thread_id={self.thread_id}, direction={self.direction})>"


class WhatsAppTemplateModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Database model for WhatsApp templates."""

    __tablename__ = "whatsapp_templates"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_connection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.channel_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_template_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    components: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<WhatsAppTemplateModel(id={self.id}, name={self.name}, status={self.status})>"


class CatalogMappingModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Database model for catalog product mappings."""

    __tablename__ = "catalog_mappings"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_connection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.channel_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_catalog_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_product_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<CatalogMappingModel(id={self.id}, product_id={self.product_id}, sync_status={self.sync_status})>"


class WebhookEventModel(Base, UUIDMixin, TimestampMixin):
    """Database model for webhook events (audit/DLQ)."""

    __tablename__ = "webhook_events"
    __table_args__ = {"schema": "public"}

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<WebhookEventModel(id={self.id}, provider={self.provider}, status={self.status})>"


class CapiEventModel(Base, UUIDMixin, TimestampMixin):
    """Database model for CAPI events (audit)."""

    __tablename__ = "capi_events"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_code: Mapped[int | None] = mapped_column(nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"<CapiEventModel(id={self.id}, event_name={self.event_name}, store_id={self.store_id})>"
