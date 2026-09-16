"""A shopper asking for something the store does not list.

Bookshops get this constantly — "do you have this edition?", with a photo of
a cover from somewhere else — and until now it arrived in a DM, a WhatsApp
thread or nowhere at all. This is the record of that ask: who wants it, what
they described, the photos they attached, and whether the merchant has done
anything about it yet.

Not a product, not an order, not a support case: it is a lead the merchant can
answer with a price. `customer_id` is a loose reference rather than a foreign
key because most requests come from someone who has never ordered.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class ProductRequestModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "product_requests"
    __table_args__ = (
        Index("ix_product_requests_store_status", "store_id", "status"),
        Index("ix_product_requests_store_created", "store_id", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: What the shopper is after, in their own words — title, ISBN, edition.
    details: Mapped[str] = mapped_column(Text, nullable=False)

    #: CDN URLs of the photos attached to the request.
    images: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    #: new → contacted → sourced | closed. Free-form on purpose: the merchant's
    #: workflow is theirs, and a rigid enum would need a migration to extend.
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="new", server_default="new"
    )

    #: Merchant's own note, never shown to the shopper.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Which storefront page the request came from, and in which language.
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(8), nullable=True)

    #: Set when the request stops being "new" — what the hub sorts unanswered
    #: requests by.
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
