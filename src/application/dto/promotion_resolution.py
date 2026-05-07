"""DTOs for the storefront `GET /storefront/.../promotions/active` response.

Shape that the bazaar (Next.js) reads to render the announcement bar,
popups, floating widget, cookie banner, etc.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.dto.promotion import PromotionDisplayOutput
from src.core.enums.promotion_enums import PromotionSurface
from src.core.value_objects.discount_rule import DiscountRule


class VisitorContextInput(BaseModel):
    """Visitor info the storefront passes when fetching active promos."""

    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    visitor_token: str | None = Field(default=None, max_length=64)
    customer_tags: list[str] = Field(default_factory=list)
    cart_subtotal_cents: int = 0
    cart_product_ids: list[UUID] = Field(default_factory=list)
    cart_category_ids: list[UUID] = Field(default_factory=list)
    country: str | None = None
    city: str | None = None
    device: str = "desktop"
    is_first_visit: bool = False
    is_logged_in: bool = False
    page_path: str = "/"
    locale: str = "ar"


class ResolvedPromotionOutput(BaseModel):
    """One promotion + the chosen display, ready to render."""

    model_config = ConfigDict(from_attributes=True)

    promotion_id: UUID
    surface: PromotionSurface
    priority: int
    content: dict[str, Any]
    translated_content: dict[str, Any] = Field(default_factory=dict)
    discount_rule: DiscountRule | None = None
    coupon_code: str | None = None
    display: PromotionDisplayOutput | None = None
    fingerprint: str


class ActivePromotionsOutput(BaseModel):
    """Grouped-by-surface response."""

    announcement_bars: list[ResolvedPromotionOutput] = Field(default_factory=list)
    popups: list[ResolvedPromotionOutput] = Field(default_factory=list)
    floating_widgets: list[ResolvedPromotionOutput] = Field(default_factory=list)
    cookie_banner: ResolvedPromotionOutput | None = None
    auto_discounts: list[ResolvedPromotionOutput] = Field(default_factory=list)
    discount_codes_visible: list[ResolvedPromotionOutput] = Field(default_factory=list)
    resolved_at: datetime
    cache_ttl_seconds: int = 60


class CartDiscountsOutput(BaseModel):
    """Returned by `CalculateCartDiscountsUseCase`."""

    model_config = ConfigDict(extra="forbid")

    code_discount_cents: int = 0
    automatic_discount_cents: int = 0
    free_shipping: bool = False
    applied_promotion_ids: list[UUID] = Field(default_factory=list)
    rejected: list[dict[str, str]] = Field(default_factory=list)

    @property
    def total_discount_cents(self) -> int:
        return self.code_discount_cents + self.automatic_discount_cents
