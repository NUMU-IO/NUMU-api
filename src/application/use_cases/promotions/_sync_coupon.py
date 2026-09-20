"""Keep the linked coupon's stored discount in step with its promotion rule."""

from decimal import Decimal

from src.core.entities.coupon import Coupon, CouponType
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.value_objects.discount_rule import DiscountRule, DiscountRuleKind


async def sync_coupon_rule(
    coupon_repo: ICouponRepository,
    coupon: Coupon,
    rule: DiscountRule | None,
    *,
    activate: bool = False,
) -> None:
    if activate:
        coupon.is_active = True
    if rule is None:
        if activate:
            await coupon_repo.update(coupon)
        return
    kind = rule.kind
    if kind == DiscountRuleKind.TIERED:
        coupon.coupon_type = CouponType.TIERED
        coupon.value = Decimal(0)
        coupon.config = {
            "tiers": [
                {
                    "min_subtotal_cents": t.threshold_cents,
                    "discount_percentage": t.percent,
                }
                for t in rule.tiers
            ]
        }
    elif kind == DiscountRuleKind.PERCENTAGE:
        coupon.coupon_type = CouponType.PERCENTAGE
        coupon.value = Decimal(rule.value_percent or 0)
        coupon.config = None
    elif kind == DiscountRuleKind.FIXED:
        coupon.coupon_type = CouponType.FIXED
        coupon.value = Decimal(rule.value_cents or 0) / Decimal(100)
        coupon.config = None
    elif kind == DiscountRuleKind.FREE_SHIPPING:
        coupon.coupon_type = CouponType.FREE_SHIPPING
        coupon.value = Decimal(0)
        coupon.config = None
    else:
        if activate:
            await coupon_repo.update(coupon)
        return
    coupon.min_order_amount = (
        Decimal(rule.min_subtotal_cents) / Decimal(100)
        if rule.min_subtotal_cents is not None
        else None
    )
    coupon.max_discount_amount = (
        Decimal(rule.max_discount_cents) / Decimal(100)
        if rule.max_discount_cents is not None
        else None
    )
    await coupon_repo.update(coupon)
