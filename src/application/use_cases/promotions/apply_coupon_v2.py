"""ApplyCouponV2UseCase — successor to apply_coupon, promotion-aware.

Backwards compatible: a coupon with no linked promotion behaves
identically to the legacy `ApplyCouponUseCase`. When a promotion is
linked, we additionally honor its targeting / scheduling rules.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.application.dto.promotion_resolution import VisitorContextInput
from src.core.entities.coupon import CouponType
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.promotion_event_repository import (
    IPromotionEventRepository,
)
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionRepository,
    IPromotionTargetRepository,
)
from src.core.services.promotion_eligibility_checker import (
    EligibilityContext,
    PromotionEligibilityChecker,
)
from src.core.value_objects.discount_rule import DiscountContext


class ApplyCouponV2Output(BaseModel):
    """Result of applying a coupon code at checkout."""

    model_config = ConfigDict(extra="forbid")

    coupon_id: UUID
    code: str
    coupon_type: str
    discount_amount: Decimal
    free_shipping: bool
    promotion_id: UUID | None = None


class ApplyCouponV2UseCase:
    """Apply a coupon code, layering promotion targeting if linked."""

    def __init__(
        self,
        *,
        coupon_repo: ICouponRepository,
        promotion_repo: IPromotionRepository,
        target_repo: IPromotionTargetRepository,
        event_repo: IPromotionEventRepository,
        eligibility_checker: PromotionEligibilityChecker,
    ) -> None:
        self._coupon_repo = coupon_repo
        self._promotion_repo = promotion_repo
        self._target_repo = target_repo
        self._event_repo = event_repo
        self._checker = eligibility_checker

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        code: str,
        order_amount: Decimal,
        visitor: VisitorContextInput | None = None,
    ) -> ApplyCouponV2Output:
        coupon = await self._coupon_repo.get_by_code(store_id, code)
        if coupon is None:
            raise EntityNotFoundError("Coupon", code, identifier_name="code")
        linked = await self._promotion_repo.get_by_coupon_id(store_id, coupon.id)
        usable = (
            coupon.is_active and coupon.has_remaining_uses
            if linked is not None
            else coupon.is_usable
        )
        if not usable:
            raise ValidationError("This coupon cannot be applied")
        if linked is None and not coupon.meets_minimum_order(order_amount):
            raise ValidationError(
                f"Order total must be at least {coupon.min_order_amount} "
                f"to use this coupon"
            )

        # Include inactive promotions so a draft or paused offer cannot fall
        # through to the coupon's stored amount.
        linked_promo_id: UUID | None = None
        if linked is not None:
            if visitor is None:
                raise ValidationError("Visitor context is required for this offer")
            now = datetime.now(UTC)
            targets = await self._target_repo.list_for_promotion(linked.id)
            total_count = 0
            customer_count = 0
            if linked.usage_limit_total is not None:
                total_count = (
                    await self._event_repo.counts_for_promotion(linked.id)
                ).conversions
            if (
                linked.usage_limit_per_customer is not None
                and visitor.customer_id is not None
            ):
                customer_count = await self._event_repo.count_conversions_for_customer(
                    linked.id, visitor.customer_id
                )
            ctx = EligibilityContext(
                customer_id=visitor.customer_id,
                customer_tags=visitor.customer_tags,
                cart_subtotal_cents=visitor.cart_subtotal_cents,
                cart_product_ids=visitor.cart_product_ids,
                cart_category_ids=visitor.cart_category_ids,
                country=visitor.country,
                city=visitor.city,
                device=visitor.device,
                is_first_visit=visitor.is_first_visit,
                is_logged_in=visitor.is_logged_in,
                convert_count_total=total_count,
                convert_count_per_customer=customer_count,
            )
            verdict = self._checker.is_eligible(linked, targets, ctx, now=now)
            if not verdict.eligible:
                raise ValidationError(
                    "This coupon's promotion is not available right now: "
                    + (verdict.reasons[0] if verdict.reasons else "blocked")
                )
            linked_promo_id = linked.id

        discount_amount = coupon.calculate_discount(order_amount)
        free_shipping = coupon.coupon_type == CouponType.FREE_SHIPPING
        if linked is not None:
            if linked.discount_rule is None:
                raise ValidationError("This coupon's promotion has no discount rule")
            result = linked.discount_rule.calculate(
                DiscountContext(subtotal_cents=int(order_amount * 100), line_items=[])
            )
            discount_amount = Decimal(result.discount_cents) / Decimal(100)
            free_shipping = result.free_shipping
            if discount_amount <= 0 and not free_shipping:
                raise ValidationError("This coupon does not apply to this order")

        return ApplyCouponV2Output(
            coupon_id=coupon.id,
            code=coupon.code,
            coupon_type=coupon.coupon_type.value,
            discount_amount=discount_amount,
            free_shipping=free_shipping,
            promotion_id=linked_promo_id,
        )
