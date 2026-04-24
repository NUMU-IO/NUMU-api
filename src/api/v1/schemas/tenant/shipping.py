"""Pydantic request/response schemas for the shipping configuration API.

Split across three consumer audiences:
    * Merchant dashboard: zone / rate CRUD, coverage, rate calculator.
    * Storefront: governorate list + /shipping/options at checkout.
    * Public: canonical governorate reference.

Rate config payloads are the discriminated-union models from
`src.core.entities.shipping_rate` re-exported here for convenience.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.entities.shipping_rate import (
    RateConfigCarrierApi,
    RateConfigFlat,
    RateConfigFreeOver,
    RateConfigWeightBand,
    RateType,
    WeightBand,
)
from src.core.value_objects.geography import LogisticsZone

__all__ = [
    # rate config re-exports
    "RateConfigCarrierApi",
    "RateConfigFlat",
    "RateConfigFreeOver",
    "RateConfigWeightBand",
    "RateType",
    "WeightBand",
    # reference
    "GovernorateResponse",
    # merchant CRUD
    "CreateZoneRequest",
    "UpdateZoneRequest",
    "CreateRateRequest",
    "UpdateRateRequest",
    "ZoneResponse",
    "RateResponse",
    "CoverageResponse",
    "PresetResponse",
    # storefront
    "StorefrontGovernorateResponse",
    "ShippingOptionsRequest",
    "ShippingOptionResponse",
    "FreeShippingProgressResponse",
    "ShippingOptionsResponse",
]


# ─── Reference ────────────────────────────────────────────────────────


class GovernorateResponse(BaseModel):
    """Canonical governorate record for the public reference endpoint."""

    code: str = Field(description="ISO 3166-2 code, e.g. 'EG-C'")
    name: str
    name_en: str
    name_ar: str
    default_zone: LogisticsZone
    capital: str


# ─── Rate config union for merchant CRUD ─────────────────────────────

RateConfigInput = Annotated[
    RateConfigFlat | RateConfigFreeOver | RateConfigWeightBand | RateConfigCarrierApi,
    Field(discriminator="type"),
]


# ─── Merchant: Zone CRUD ─────────────────────────────────────────────


class CreateZoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, max_length=100)
    governorate_codes: list[str] = Field(
        min_length=1,
        description="ISO 3166-2 codes the zone covers",
    )
    estimated_days_min: int = Field(default=2, ge=0, le=365)
    estimated_days_max: int = Field(default=5, ge=0, le=365)
    cod_enabled: bool = True
    cod_fee_cents: int = Field(default=0, ge=0)
    is_active: bool = True
    sort_order: int = 0


class UpdateZoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, max_length=100)
    governorate_codes: list[str] | None = Field(
        default=None,
        description="If provided, replaces zone membership atomically.",
    )
    estimated_days_min: int | None = Field(default=None, ge=0, le=365)
    estimated_days_max: int | None = Field(default=None, ge=0, le=365)
    cod_enabled: bool | None = None
    cod_fee_cents: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    sort_order: int | None = None


class RateResponse(BaseModel):
    """Shape returned to the merchant dashboard."""

    id: UUID
    zone_id: UUID
    rate_type: RateType
    label: str
    label_ar: str | None
    config: dict
    is_active: bool
    sort_order: int


class ZoneResponse(BaseModel):
    id: UUID
    store_id: UUID
    name: str
    name_ar: str | None
    governorate_codes: list[str]
    estimated_days_min: int
    estimated_days_max: int
    cod_enabled: bool
    cod_fee_cents: int
    is_active: bool
    sort_order: int
    rates: list[RateResponse] = Field(default_factory=list)


# ─── Merchant: Rate CRUD ─────────────────────────────────────────────


class CreateRateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    label_ar: str | None = Field(default=None, max_length=100)
    config: RateConfigInput
    is_active: bool = True
    sort_order: int = 0


class UpdateRateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=100)
    label_ar: str | None = Field(default=None, max_length=100)
    config: RateConfigInput | None = None
    is_active: bool | None = None
    sort_order: int | None = None


# ─── Coverage ────────────────────────────────────────────────────────


class CoverageConflict(BaseModel):
    governorate_code: str
    zones: list[UUID]


class CoverageResponse(BaseModel):
    """Returned by GET /stores/{id}/shipping/coverage.

    `covered` + `uncovered` partition the 27 canonical governorate codes.
    `conflicts` lists any governorate that (somehow) belongs to multiple
    active zones — should always be empty, but the dashboard surfaces it
    as a banner if something slipped through.
    """

    covered: list[str]
    uncovered: list[str]
    conflicts: list[CoverageConflict] = Field(default_factory=list)


class PresetResponse(BaseModel):
    """One-shot result of applying the Egypt 4-zone preset."""

    created_zone_ids: list[UUID]
    assigned_governorate_codes: list[str]


# ─── Storefront ──────────────────────────────────────────────────────


class StorefrontGovernorateResponse(BaseModel):
    """Trimmed shape for the storefront dropdown."""

    code: str
    name: str


class ShippingOptionsRequest(BaseModel):
    """Body for POST /storefront/store/{id}/shipping/options."""

    model_config = ConfigDict(extra="forbid")

    governorate_code: str = Field(description="ISO 3166-2 code, e.g. 'EG-C'")
    cart_subtotal_cents: int = Field(ge=0)
    cart_weight_g: int = Field(default=0, ge=0)
    cod_requested: bool = False
    coupon_code: str | None = None


class ShippingOptionResponse(BaseModel):
    rate_id: UUID
    label: str
    label_ar: str | None
    amount_cents: int
    currency: str
    estimated_days_min: int
    estimated_days_max: int
    cod_supported: bool
    rate_type: RateType


class FreeShippingProgressResponse(BaseModel):
    current_cents: int
    threshold_cents: int
    remaining_cents: int
    qualified: bool


class ShippingOptionsResponse(BaseModel):
    options: list[ShippingOptionResponse]
    free_shipping_progress: FreeShippingProgressResponse | None = None


# ─── Merchant preview (for RateCalculator) ────────────────────────


class RateCalculatorRequest(ShippingOptionsRequest):
    """Identical shape to the storefront options request. Reuses the
    storefront endpoint's Pydantic model so the merchant rate calculator
    preview always matches what the customer sees.

    Kept as a subclass (not an alias) so OpenAPI shows it under the
    merchant namespace too.
    """

    # Subclass exists only for API surface clarity; no extra fields.
    pass


# Literal tag used by the preset endpoint — reserved for future presets.
PresetName = Literal["egypt-4-zone"]
