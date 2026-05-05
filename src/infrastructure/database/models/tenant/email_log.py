"""Email log database model (transactional email send audit trail)."""

from uuid import UUID as PyUUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class EmailLogModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Audit-trail row for a single transactional email send attempt.

    Linked to an ``email_templates`` row via ``template_id`` when a custom
    template was used; the FK uses ``ON DELETE SET NULL`` so deleting a
    template never destroys historical send records.
    """

    __tablename__ = "email_logs"
    __table_args__ = (
        Index("idx_email_logs_store", "store_id"),
        Index("idx_email_logs_message_id", "message_id"),
        Index("idx_email_logs_tenant", "tenant_id"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    template_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.email_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued"
    )  # queued | sent | failed | delivered
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    used_custom_template: Mapped[bool] = mapped_column(default=False, nullable=False)
    # See EmailTemplateModel.extra_data — name avoids SQLAlchemy ``metadata``
    # collision.
    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    store = relationship("StoreModel", lazy="noload")
    template = relationship("EmailTemplateModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<EmailLog(id={self.id}, recipient={self.recipient}, "
            f"status={self.status})>"
        )
