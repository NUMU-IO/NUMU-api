"""WhatsApp access-request model (public schema).

Platform-level entitlement gate for the WhatsApp Business channel. A merchant
must request access per store; a platform admin approves/rejects, and can later
disable (kill-switch) or re-enable an approved store. This is the gate that sits
*above* the existing per-store WhatsApp connection flow (BYO / platform-managed)
— a store cannot connect a number or turn on notifications until its access row
is ``APPROVED``.

One row per store (``store_id`` is unique). The ``status`` column is the single
canonical FSM:

    (no row) --request--> PENDING --approve--> APPROVED --disable--> DISABLED
                              |                     ^                    |
                              +-------reject----> REJECTED              |
                              ^                     |                   |
                              +------(re-request)---+     enable--------+

Mirrors ``access_request.py`` (public schema, plain UUID columns, enum persisted
as UPPERCASE member names — no ``values_callable``; the DB enum labels are the
member names, the API surfaces the lowercase ``.value``).
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Enum, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class WhatsAppAccessStatus(StrEnum):
    """Canonical FSM state of a store's WhatsApp access entitlement.

    Persisted as the UPPERCASE member name (see module docstring). ``none``
    (no request yet) is not a DB state — it is represented API-side by the
    absence of a row.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISABLED = "disabled"


class WhatsAppAccessRequestModel(Base, UUIDMixin, TimestampMixin):
    """A store's request for (and entitlement to) the WhatsApp channel.

    Reviewed by platform admins (SUPER_ADMIN) via the admin API. Exactly one
    row per store — re-requesting after a rejection flips the same row back to
    ``PENDING`` rather than inserting a duplicate.
    """

    __tablename__ = "whatsapp_access_requests"
    __table_args__ = (
        UniqueConstraint("store_id", name="uq_whatsapp_access_store"),
        Index("ix_whatsapp_access_requests_tenant", "tenant_id"),
        Index("ix_whatsapp_access_requests_status", "status"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    status: Mapped[WhatsAppAccessStatus] = mapped_column(
        Enum(WhatsAppAccessStatus, name="whatsappaccessstatus", schema="public"),
        default=WhatsAppAccessStatus.PENDING,
        nullable=False,
    )
    requester_user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    # Merchant-supplied context captured on the request form.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expected_volume: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Review metadata — set on any admin transition (approve/reject/disable/enable).
    reviewer_user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<WhatsAppAccessRequestModel(store_id={self.store_id}, "
            f"status={self.status})>"
        )
