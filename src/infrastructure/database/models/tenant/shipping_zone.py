"""ShippingZone database model (public schema with tenant_id discriminator).

Merchant-defined zones that group canonical Egyptian governorates.
Rates hang off zones via the `shipping_rates` table; governorate
membership is M2M via `shipping_zone_governorates`.
"""

from sqlalchemy import Boolean, ForeignKey, Integer, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ShippingZoneModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Merchant-defined shipping zone, scoped to a store."""

    __tablename__ = "shipping_zones"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_ar: Mapped[str | None] = mapped_column(String(100), nullable=True)
    estimated_days_min: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=2, server_default="2"
    )
    estimated_days_max: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=5, server_default="5"
    )
    cod_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    cod_fee_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Relationships
    governorates = relationship(
        "ShippingZoneGovernorateModel",
        back_populates="zone",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    rates = relationship(
        "ShippingRateModel",
        back_populates="zone",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ShippingZoneModel(id={self.id}, store={self.store_id}, name={self.name!r})>"
