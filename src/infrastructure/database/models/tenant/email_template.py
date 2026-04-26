"""Email template database model (per-store transactional email overrides)."""

from uuid import UUID as PyUUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class EmailTemplateModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Per-store, per-event, per-language email template override.

    The unique index on ``(store_id, event_type, language)`` guarantees
    at most one custom template exists for any send. When no row matches
    a send request, the platform falls back to its built-in defaults.
    """

    __tablename__ = "email_templates"
    __table_args__ = (
        Index("idx_email_templates_store", "store_id"),
        Index(
            "idx_email_templates_store_event_lang",
            "store_id",
            "event_type",
            "language",
            unique=True,
        ),
        Index("idx_email_templates_tenant", "tenant_id"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="ar")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # NOTE: column is named ``extra_data`` (NOT ``metadata``) because
    # ``metadata`` is reserved on SQLAlchemy DeclarativeBase.
    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    store = relationship("StoreModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<EmailTemplate(id={self.id}, event_type={self.event_type}, "
            f"language={self.language})>"
        )
