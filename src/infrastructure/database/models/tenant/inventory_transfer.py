"""InventoryTransfer DB model — Phase 8.2.

Lines stored as embedded JSONB rather than a separate table — typical
transfer has <20 lines and we always read/write a transfer as a
whole. A separate `inventory_transfer_lines` table would just be
extra indirection.
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.inventory_transfer import TransferStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class InventoryTransferModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "inventory_transfers"
    __table_args__ = (
        Index("ix_transfers_store_status", "store_id", "status"),
        Index("ix_transfers_from_location", "from_location_id"),
        Index("ix_transfers_to_location", "to_location_id"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[TransferStatus] = mapped_column(
        Enum(
            TransferStatus,
            name="transferstatus",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=TransferStatus.DRAFT,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    carrier_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # JSONB array of `{variant_id: UUID, quantity: int}` entries.
    lines: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
