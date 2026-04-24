"""ShippingZone domain entity — a merchant-defined grouping of governorates.

Distinct from `LogisticsZone` (in `value_objects.geography`), which is a
global preset grouping used to seed the Quick Start 4-zone setup. A
`ShippingZone` is per-store and per-merchant-choice; merchants can have
anywhere from 1 zone (flat national rate) to 27 (per-governorate).

Invariants:
    - A governorate must belong to at most ONE active zone per store.
      Enforced at the application/DB layer; the resolver relies on it.
    - At least one governorate and one active rate required to be saved
      as active.
"""

from uuid import UUID

from pydantic import Field, field_validator

from src.core.entities.base import BaseEntity


class ShippingZone(BaseEntity):
    """A merchant-defined shipping zone.

    Governorate membership is persisted separately (M2M table
    `shipping_zone_governorates`) and exposed here as
    `governorate_codes: list[str]` for read paths.
    """

    tenant_id: UUID
    store_id: UUID
    name: str = Field(min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, max_length=100)
    estimated_days_min: int = Field(default=2, ge=0, le=365)
    estimated_days_max: int = Field(default=5, ge=0, le=365)
    cod_enabled: bool = True
    cod_fee_cents: int = Field(default=0, ge=0)
    is_active: bool = True
    sort_order: int = 0
    # Read-side convenience: populated by repository when hydrating.
    # Writes go through explicit set_governorates() on the repository.
    governorate_codes: list[str] = Field(default_factory=list)

    @field_validator("estimated_days_max")
    @classmethod
    def _max_at_least_min(cls, v: int, info) -> int:
        """Estimated days max must be ≥ min."""
        min_days = info.data.get("estimated_days_min")
        if min_days is not None and v < min_days:
            raise ValueError(
                f"estimated_days_max ({v}) must be >= estimated_days_min ({min_days})"
            )
        return v

    @property
    def estimated_days_display(self) -> str:
        """Human-readable ETA string for the merchant UI."""
        if self.estimated_days_min == self.estimated_days_max:
            return f"{self.estimated_days_min} days"
        return f"{self.estimated_days_min}-{self.estimated_days_max} days"
