"""Promotion DTOs (Pydantic v2).

Request shapes for the merchant API plus output shapes for both the
merchant admin and storefront. The discounted-content union is
discriminated on `surface`, mirroring the entity layer.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic import Discriminator as PydanticDiscriminator

from src.core.enums.promotion_enums import (
    DisplayFrequency,
    DisplayTrigger,
    PromotionEventType,  # noqa: F401 — re-exported for convenience
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.value_objects.discount_rule import DiscountRule
from src.core.value_objects.localized_promotion_content import (
    LocalizedPromotionContent,
)
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    AutomaticContent,
    CookieBannerContent,
    DiscountCodeContent,
    FloatingWidgetContent,
    PopupContent,
)

# Re-use the core discriminated union for content payloads. The
# application layer doesn't define its own variant — that would just
# duplicate the schema and drift over time.
PromotionContentPayload = Annotated[
    DiscountCodeContent
    | AutomaticContent
    | AnnouncementBarContent
    | PopupContent
    | FloatingWidgetContent
    | CookieBannerContent,
    PydanticDiscriminator("surface"),
]


# --------------------------------------------------------------------------- #
# Inputs                                                                      #
# --------------------------------------------------------------------------- #


class PromotionDisplayInput(BaseModel):
    """Trigger / frequency / page-target rule attached to a promotion."""

    model_config = ConfigDict(extra="forbid")

    trigger: DisplayTrigger
    trigger_value: dict[str, Any] = Field(default_factory=dict)
    frequency: DisplayFrequency
    pages: list[str] = Field(default_factory=list)
    device_targets: list[str] = Field(default_factory=lambda: ["desktop", "mobile"])
    is_enabled: bool = True


class PromotionTargetInput(BaseModel):
    """Audience / catalog / geo rule attached to a promotion."""

    model_config = ConfigDict(extra="forbid")

    target_kind: TargetKind
    target_value: dict[str, Any] = Field(default_factory=dict)
    inclusion: bool = True
    # BOGO line-set tagging: `buy_set` / `get_set` targets bypass the
    # eligibility checker and feed the discount calculator's per-line
    # filters. `None` (default) preserves the legacy global-filter
    # semantics. Mirrors `PromotionTarget.role` on the entity.
    role: Literal["buy_set", "get_set"] | None = None


class CreatePromotionInput(BaseModel):
    """Payload for `POST /stores/{id}/promotions`."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    surface: PromotionSurface
    status: PromotionStatus = PromotionStatus.DRAFT

    coupon_id: UUID | None = None
    discount_rule: DiscountRule | None = None
    content: PromotionContentPayload

    translations: dict[str, LocalizedPromotionContent] = Field(default_factory=dict)
    displays: list[PromotionDisplayInput] = Field(default_factory=list)
    targets: list[PromotionTargetInput] = Field(default_factory=list)

    priority: int = 0
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    # Per-promotion usage caps. Mirrors the fields on the Promotion entity
    # (`Promotion.usage_limit_total` / `usage_limit_per_customer`); without
    # them on the DTO, the merchant form's payload tripped Pydantic's
    # `extra="forbid"` and the create request 422'd.
    usage_limit_total: int | None = Field(default=None, ge=1)
    usage_limit_per_customer: int | None = Field(default=None, ge=1)


class UpdatePromotionInput(BaseModel):
    """Payload for `PATCH /stores/{id}/promotions/{pid}`.

    Every field is optional. `version` is required for optimistic
    locking — clients echo the version they read.
    """

    model_config = ConfigDict(extra="forbid")

    version: int

    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: PromotionStatus | None = None
    coupon_id: UUID | None = None
    discount_rule: DiscountRule | None = None
    content: PromotionContentPayload | None = None
    translations: dict[str, LocalizedPromotionContent] | None = None
    displays: list[PromotionDisplayInput] | None = None
    targets: list[PromotionTargetInput] | None = None
    priority: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    # See `CreatePromotionInput.usage_limit_total` — same fields, also
    # required on update for the merchant edit form's PATCH to validate.
    usage_limit_total: int | None = Field(default=None, ge=1)
    usage_limit_per_customer: int | None = Field(default=None, ge=1)


# --------------------------------------------------------------------------- #
# Outputs                                                                     #
# --------------------------------------------------------------------------- #


class PromotionDisplayOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trigger: DisplayTrigger
    trigger_value: dict[str, Any]
    frequency: DisplayFrequency
    pages: list[str]
    device_targets: list[str]
    is_enabled: bool


class PromotionTargetOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    target_kind: TargetKind
    target_value: dict[str, Any]
    inclusion: bool
    role: Literal["buy_set", "get_set"] | None = None


class PromotionMetricsBlock(BaseModel):
    """Aggregate counts displayed on the merchant detail page."""

    model_config = ConfigDict(from_attributes=True)

    impressions: int = 0
    clicks: int = 0
    dismissals: int = 0
    redemptions: int = 0
    conversions: int = 0
    revenue_cents: int = 0


class PromotionOutput(BaseModel):
    """Full read model — for merchant admin detail page."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID
    name: str
    surface: PromotionSurface
    status: PromotionStatus

    coupon_id: UUID | None = None
    discount_rule: DiscountRule | None = None
    content: dict[str, Any]
    translations: dict[str, LocalizedPromotionContent] = Field(default_factory=dict)
    displays: list[PromotionDisplayOutput] = Field(default_factory=list)
    targets: list[PromotionTargetOutput] = Field(default_factory=list)

    priority: int
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime

    # Surfaced on the detail response so the merchant edit form can
    # round-trip the caps (it preloads `promo.usage_limit_total` into
    # the field state).
    usage_limit_total: int | None = None
    usage_limit_per_customer: int | None = None

    metrics: PromotionMetricsBlock = Field(default_factory=PromotionMetricsBlock)


class PromotionListItemOutput(BaseModel):
    """Slim shape used in list/index responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    surface: PromotionSurface
    status: PromotionStatus
    priority: int
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    version: int
    coupon_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class PromotionListOutput(BaseModel):
    items: list[PromotionListItemOutput]
    total: int
    limit: int
    offset: int
