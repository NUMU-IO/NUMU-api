"""InventoryLevel DB model — Phase 8.2.

(variant × location) join carrying the per-location stock count.
The sum across rows for a given variant equals
`product_variants.inventory_quantity` (the application layer keeps
them consistent).

Unique constraint on `(variant_id, location_id)` prevents two rows
for the same pair; the upsert pattern in the repository uses
ON CONFLICT to handle "initialize level for new variant" idempotently.
"""

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class InventoryLevelModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "inventory_levels"
    __table_args__ = (
        UniqueConstraint(
            "variant_id", "location_id", name="uq_inventory_variant_location"
        ),
        # Hot path: list levels for a variant (PDP per-location chip).
        Index("ix_inventory_levels_variant", "variant_id"),
        # Hub Inventory page: list every level at a given location.
        Index("ix_inventory_levels_location", "location_id"),
        # Per-store rollup query — used by hub Inventory dashboard.
        Index("ix_inventory_levels_store", "store_id"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.product_variants.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
