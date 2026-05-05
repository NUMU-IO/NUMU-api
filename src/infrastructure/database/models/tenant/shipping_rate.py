"""ShippingRate database model.

A rate belongs to a zone. Type-specific config lives in a JSONB column;
Pydantic discriminated-union models in
`src.core.entities.shipping_rate` validate the shape on write.
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ShippingRateModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A single rate offering within a zone.

    Multiple rates per zone surface as multiple options at checkout
    (e.g. Standard + Express).
    """

    __tablename__ = "shipping_rates"
    __table_args__ = {"schema": "public"}

    zone_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.shipping_zones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Free-text rate type instead of a DB enum so adding new types
    # later doesn't require a schema migration. Validated at the
    # application layer against `RateType` enum.
    rate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    label_ar: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Type-specific config; validated by `parse_rate_config()` on read/write.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    zone = relationship("ShippingZoneModel", back_populates="rates")

    def __repr__(self) -> str:
        return (
            f"<ShippingRateModel(id={self.id}, zone={self.zone_id}, "
            f"type={self.rate_type!r}, label={self.label!r})>"
        )
