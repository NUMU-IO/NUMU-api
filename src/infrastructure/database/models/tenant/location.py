"""Location DB model — Phase 7.2."""

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class LocationModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "locations"
    __table_args__ = (
        # Hot path for the storefront's pickup picker: enabled, pickup-
        # capable locations for a store, position-ordered.
        Index(
            "ix_locations_pickup",
            "store_id",
            "position",
            postgresql_where="is_active = true AND fulfills_pickup = true",
        ),
        Index("ix_locations_store", "store_id"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_ar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Address stored as JSONB — the Address entity is value-typed,
    # not a separate table, and storing it inline avoids a join on
    # every location read.
    address: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fulfills_orders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fulfills_pickup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    pickup_instructions: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pickup_instructions_ar: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
