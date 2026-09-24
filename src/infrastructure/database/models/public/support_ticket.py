"""Support threads: a merchant asking an app's partner (``kind='app'``) and a
partner asking NUMU (``kind='partner'``)."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class SupportTicketModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint("kind IN ('app', 'partner')", name="ck_support_tickets_kind"),
        CheckConstraint(
            "status IN ('open', 'answered', 'closed')",
            name="ck_support_tickets_status",
        ),
        Index("ix_support_tickets_store", "store_id"),
        Index("ix_support_tickets_app", "app_id"),
        Index("ix_support_tickets_partner", "partner_id"),
        {"schema": "public"},
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    app_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=True,
    )
    store_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=True,
    )
    partner_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.partner_accounts.id", ondelete="CASCADE"),
        nullable=True,
    )
    opened_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SupportMessageModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "support_messages"
    __table_args__ = (
        CheckConstraint(
            "author_role IN ('merchant', 'partner', 'staff')",
            name="ck_support_messages_author_role",
        ),
        Index("ix_support_messages_ticket_created", "ticket_id", "created_at"),
        {"schema": "public"},
    )

    ticket_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.support_tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_role: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
