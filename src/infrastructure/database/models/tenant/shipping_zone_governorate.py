"""M2M table linking shipping_zones to canonical governorate codes.

The governorate is referenced by its string code (ISO 3166-2, e.g.
"EG-C"), not an FK — canonical governorate data lives in code, not in
the database. This is intentional: couriers need stable codes, so
tenant-editable governorate rows would be a footgun.

Critical invariant: a governorate belongs to AT MOST ONE active zone per
store. Enforced at the application layer in the repository (not as a
partial unique index, because Postgres can't put `EXISTS` in a partial
index predicate — see design doc §2).
"""

from sqlalchemy import ForeignKey, PrimaryKeyConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base


class ShippingZoneGovernorateModel(Base):
    """Zone ↔ governorate-code link row."""

    __tablename__ = "shipping_zone_governorates"
    __table_args__ = (
        PrimaryKeyConstraint(
            "zone_id", "governorate_code", name="pk_shipping_zone_gov"
        ),
        {"schema": "public"},
    )

    zone_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.shipping_zones.id", ondelete="CASCADE"),
        nullable=False,
    )
    governorate_code: Mapped[str] = mapped_column(String(10), nullable=False)
    # Denormalized for RLS (the link table inherits the zone's tenant).
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalized for the "only-one-active-zone-per-governorate-per-store" check.
    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    zone = relationship("ShippingZoneModel", back_populates="governorates")

    def __repr__(self) -> str:
        return (
            f"<ShippingZoneGovernorateModel(zone={self.zone_id}, "
            f"governorate={self.governorate_code})>"
        )
