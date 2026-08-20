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


class EligibleLegOutput(BaseModel):
    """One leg of a BUNDLE, with the catalogue a shopper may fill it from.

    Positionally aligned with `DiscountRule.bundle_legs` — index `i` here is
    leg `i` there, and is the leg the promotion's `role="leg:{i}"` targets
    scope. A theme renders "1 tee + 1 cap" from `quantity`/`label` and offers a
    picker per leg from the ids.

    Both id lists empty = that leg accepts anything in the store. That is a
    misconfiguration rather than a feature (see
    `discount_calculator._build_leg_filters`), but it is reported honestly
    instead of hidden, so the merchant can see it on their own bundle page.
    """

    model_config = ConfigDict(from_attributes=True)

    quantity: int
    label: str | None = None
    product_ids: list[str] = Field(default_factory=list)
    category_ids: list[str] = Field(default_factory=list)


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
    # Which catalog entries can take part in the rule, read off the promotion's
    # `role="buy_set"` targets. BOTH EMPTY means the whole store qualifies.
    #
    # Themes need this to tell the truth: without it a "3 for EGP 650 on
    # scarves" offer makes the cart nudge count every unit in the cart, so a
    # shopper holding one ineligible item is told "add 2 more" and then doesn't
    # get the discount. The rule's own scoping is server-side and unaffected —
    # this is display truth only, and it exposes nothing the storefront can't
    # already see by browsing the catalogue.
    eligible_product_ids: list[str] = Field(default_factory=list)
    eligible_category_ids: list[str] = Field(default_factory=list)
    # BUNDLE only, and empty for every other kind. The flat lists above stay
    # populated for a bundle too — as the UNION across legs — so a theme that
    # only knows about `eligible_*` still counts the right cart lines and only
    # a theme that wants per-leg pickers has to learn this field.
    eligible_legs: list[EligibleLegOutput] = Field(default_factory=list)


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
    # Named snapshot of the AUTOMATIC promotions that fired — mirrors the
    # order's persisted `applied_promotions` ({id, title, title_ar?, amount}),
    # so the storefront summary can show the real promo name (e.g. "Welcome
    # 10 −EGP 30") instead of a generic "Offer" line. `amount` is each
    # promotion's own contribution (integer cents) as computed by the
    # calculator; the entries sum to `automatic_discount_cents`.
    applied_promotions: list[dict] = Field(default_factory=list)
    rejected: list[dict[str, str]] = Field(default_factory=list)

    @property
    def total_discount_cents(self) -> int:
        return self.code_discount_cents + self.automatic_discount_cents
