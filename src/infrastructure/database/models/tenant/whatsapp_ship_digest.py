"""COD Autopilot daily ship digest. One row per store per local day."""

from datetime import date, datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WhatsAppShipDigestModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """The daily merchant ship-digest record (004-cod-autopilot).

    ``order_items`` freezes exactly which orders the digest listed —
    an inbound response can only ever act on those (FR-005/FR-009).
    ``processed_at`` is set exactly once (FR-008); duplicate or late
    responses are acknowledged without re-transitioning anything.
    """

    __tablename__ = "whatsapp_ship_digests"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "digest_date", name="uq_wa_ship_digests_store_date"
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    digest_date: Mapped[date] = mapped_column(Date, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Snapshot of store.contact_phone at send time — inbound replies are
    # matched against this, not the live store row.
    merchant_phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # [{"n": 1, "order_id": "...", "order_number": "..."}]
    order_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    capped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # none | all_shipped | exceptions
    response_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none"
    )
    response_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    excepted_numbers: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    store = relationship("StoreModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<WhatsAppShipDigest(store_id={self.store_id}, "
            f"date={self.digest_date}, response={self.response_type})>"
        )
