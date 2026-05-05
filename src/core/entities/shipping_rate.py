"""ShippingRate domain entity and rate-config value objects.

A rate belongs to a zone and has a `rate_type` that selects the matching
config shape. The config is stored as JSONB in the database; the
discriminated-union models below validate it on the way in and out.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.entities.base import BaseEntity


class RateType(StrEnum):
    """Supported rate types. Maps 1:1 to `rate_type` column values.

    Resolver branches on this value. Adding a new type means:
      1. Add a member here.
      2. Add a `RateConfig*` model below.
      3. Add a branch in `ShippingResolver._evaluate_rate`.
    """

    FLAT = "flat"
    FREE_OVER = "free_over"
    WEIGHT_BAND = "weight_band"
    CARRIER_API = "carrier_api"  # schema-ready, not wired in MVP


# ─── Rate config discriminated union ──────────────────────────────────
# Each model validates one rate_type's `config` JSONB payload.


class RateConfigFlat(BaseModel):
    """Flat rate — always the same amount regardless of cart."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal[RateType.FLAT] = RateType.FLAT
    amount_cents: int = Field(ge=0)


class RateConfigFreeOver(BaseModel):
    """Free shipping when cart subtotal (post-discount) ≥ threshold.

    Below the threshold, charges `amount_cents`. At or above, 0.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal[RateType.FREE_OVER] = RateType.FREE_OVER
    amount_cents: int = Field(ge=0)
    free_when_subtotal_gte_cents: int = Field(ge=0)


class WeightBand(BaseModel):
    """One band of a weight-band table.

    `max_weight_g = None` represents the final open-ended band.
    Within an open-ended band, `per_extra_kg_cents` adds the given
    amount for every started kilogram above the previous band's max.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_weight_g: int | None = Field(default=None, ge=0)
    amount_cents: int = Field(ge=0)
    per_extra_kg_cents: int | None = Field(default=None, ge=0)


class RateConfigWeightBand(BaseModel):
    """Weight-banded pricing. First matching band wins.

    Bands must be sorted ascending by `max_weight_g` at write time
    (None last). Sorted / validated by the API layer, not the resolver.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal[RateType.WEIGHT_BAND] = RateType.WEIGHT_BAND
    bands: list[WeightBand] = Field(min_length=1)


class RateConfigCarrierApi(BaseModel):
    """Live carrier-API rate — resolver branch calls IShippingService.

    Schema-ready; not wired in MVP. Kept to pin the contract so the
    future `/shipping/options` path doesn't need a schema change.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal[RateType.CARRIER_API] = RateType.CARRIER_API
    carrier: str = Field(min_length=1)
    service_code: str = Field(min_length=1)


RateConfig = Annotated[
    RateConfigFlat | RateConfigFreeOver | RateConfigWeightBand | RateConfigCarrierApi,
    Field(discriminator="type"),
]


def parse_rate_config(rate_type: RateType | str, config: dict[str, Any]) -> RateConfig:
    """Validate a raw JSONB config against its rate_type.

    Adds the `type` discriminator if absent (the DB stores the type in a
    separate column and omits it from the JSON payload).

    Raises:
        pydantic.ValidationError on invalid payload.
    """
    payload = dict(config) if config else {}
    payload.setdefault(
        "type", rate_type.value if isinstance(rate_type, RateType) else rate_type
    )
    if payload["type"] == RateType.FLAT:
        return RateConfigFlat.model_validate(payload)
    if payload["type"] == RateType.FREE_OVER:
        return RateConfigFreeOver.model_validate(payload)
    if payload["type"] == RateType.WEIGHT_BAND:
        return RateConfigWeightBand.model_validate(payload)
    if payload["type"] == RateType.CARRIER_API:
        return RateConfigCarrierApi.model_validate(payload)
    raise ValueError(f"Unknown rate_type: {payload['type']!r}")


# ─── Entity ───────────────────────────────────────────────────────────


class ShippingRate(BaseEntity):
    """A rate offering within a zone.

    Multiple rates per zone surface as multiple options at checkout
    (e.g. Standard + Express). Sort order controls display order.
    """

    tenant_id: UUID
    zone_id: UUID
    rate_type: RateType
    label: str = Field(min_length=1, max_length=100)
    label_ar: str | None = None
    # Raw config stored as dict (from JSONB). Use `parsed_config` to
    # access as the typed discriminated-union model.
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    sort_order: int = 0

    @property
    def parsed_config(self) -> RateConfig:
        """Return `config` as a validated typed model.

        Raises ValidationError if stored payload is malformed — treat as
        an invariant violation and 500 the request.
        """
        return parse_rate_config(self.rate_type, self.config)
