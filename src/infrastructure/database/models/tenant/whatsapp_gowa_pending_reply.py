"""Outstanding numbered-reply prompts sent over the GOWA transport.

Meta's quick-reply buttons carry their meaning with them: a tap comes back as
``button.payload`` = ``<action>:<subdomain>/<order_id>``, so the webhook knows
which order it refers to without looking anything up. whatsmeow has no buttons,
so a GOWA prompt arrives back as the bare text ``"1"`` — true for every order
that customer has open.

This table restores what the payload used to carry. When a numbered prompt goes
out we record, against that recipient, the exact payload each digit maps to.
An inbound digit resolves to the newest unconsumed, unexpired row and yields a
payload byte-identical to Meta's — which is why both transports can share the
existing COD handlers untouched.

Rows are consumed on use and expire on a deadline, so a stale "1" typed days
later cannot confirm an order the customer has since forgotten about.
"""

from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WhatsAppGowaPendingReplyModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A numbered prompt awaiting a digit reply."""

    __tablename__ = "whatsapp_gowa_pending_replies"
    __table_args__ = (
        # The inbound lookup: newest live prompt for this phone. Ordering by
        # created_at happens in the query; this index carries the filter.
        Index(
            "ix_wa_gowa_pending_phone_live",
            "phone",
            "expires_at",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Recipient in canonical E.164 — the only key an inbound message gives us.
    phone: Mapped[str] = mapped_column(String(20), nullable=False)

    # Which template produced the prompt. Useful for triage ("why did this
    # customer get a numbered list?") and for metrics on reply rates.
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # {"1": "confirm:nile/abc123", "2": "postpone:nile/abc123", ...}
    # Stored whole rather than as a parsed action so the webhook hands the
    # existing handlers the same string Meta would have.
    payloads: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Deadline. Without one, a digit typed a week later would act on a
    # long-settled order.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Single-use. Set when a reply is accepted, so a customer tapping "1" twice
    # doesn't drive the action twice — the handlers are idempotent, but this
    # keeps the intent explicit rather than relying on that.
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<WhatsAppGowaPendingReply phone={self.phone} "
            f"type={self.message_type} expires={self.expires_at}>"
        )
