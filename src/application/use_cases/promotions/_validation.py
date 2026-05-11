"""Surface ↔ payload validation matrix shared by create / update use cases.

Single source of truth for:

| Surface           | coupon_id | discount_rule | content.surface  |
|-------------------|-----------|---------------|------------------|
| discount_code     | required  | optional *    | discount_code    |
| automatic         | forbidden | required      | automatic        |
| announcement_bar  | forbidden | optional      | announcement_bar |
| popup             | forbidden | optional      | popup            |
| floating_widget   | forbidden | optional      | floating_widget  |
| cookie_banner     | forbidden | forbidden     | cookie_banner    |

\\* `discount_code` rule rules:
- Without a `discount_rule`, the legacy `Coupon.calculate_discount()`
  is used (handles only percentage / fixed / free_shipping).
- With a `discount_rule`, the unified `DiscountCalculator` is used,
  which additionally supports BOGO and tiered. The merchant types a
  code; the cart looks up the linked promotion and dispatches to the
  rule. The coupon row is just the human-typeable handle in this case.
"""

from src.core.enums.promotion_enums import PromotionSurface
from src.core.exceptions.promotion_exceptions import (
    CouponPromotionLinkError,
    InvalidDiscountRule,
)
from src.core.value_objects.discount_rule import DiscountRule
from src.core.value_objects.promotion_content import PromotionContent

_REQUIRES_COUPON = {PromotionSurface.DISCOUNT_CODE}
_FORBIDS_COUPON = {
    PromotionSurface.AUTOMATIC,
    PromotionSurface.ANNOUNCEMENT_BAR,
    PromotionSurface.POPUP,
    PromotionSurface.FLOATING_WIDGET,
    PromotionSurface.COOKIE_BANNER,
}
_REQUIRES_RULE = {PromotionSurface.AUTOMATIC}
# `DISCOUNT_CODE` removed — code-based BOGO / tiered need a rule on the
# promotion since the legacy Coupon entity can't represent those kinds.
_FORBIDS_RULE = {
    PromotionSurface.COOKIE_BANNER,
}


def validate_surface_payload(
    surface: PromotionSurface,
    *,
    coupon_id_set: bool,
    discount_rule: DiscountRule | None,
    content: PromotionContent | None,
) -> None:
    """Raise on inconsistent payload combinations.

    `coupon_id_set` is the boolean "did the caller send a coupon_id?",
    decoupling the logic from how the caller represents missing fields.
    """
    if surface in _REQUIRES_COUPON and not coupon_id_set:
        raise CouponPromotionLinkError("discount_code surface requires coupon_id")
    if surface in _FORBIDS_COUPON and coupon_id_set:
        raise CouponPromotionLinkError(
            f"coupon_id is forbidden for surface={surface.value}"
        )

    if surface in _REQUIRES_RULE and discount_rule is None:
        raise InvalidDiscountRule(f"surface={surface.value} requires a discount_rule")
    if surface in _FORBIDS_RULE and discount_rule is not None:
        raise InvalidDiscountRule(
            f"surface={surface.value} cannot have a discount_rule"
        )

    if content is not None and content.surface != surface.value:
        raise ValueError(
            f"content.surface ({content.surface}) "
            f"must match promotion.surface ({surface.value})"
        )
