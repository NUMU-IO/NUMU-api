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
from src.core.entities.promotion_event import PromotionEvent
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
        if not coupon.is_usable:
            raise ValidationError("This coupon cannot be applied")
        if not coupon.meets_minimum_order(order_amount):
            raise ValidationError(
                f"Order total must be at least {coupon.min_order_amount} "
                f"to use this coupon"
            )

        # Find a linked promotion if any. We don't have a `coupon → promotion`
        # back-reference column in v1 — query promotions filtered to this store
        # and pick the one whose `coupon_id` matches.
        linked_promo_id: UUID | None = None
        if visitor is not None:
            now = datetime.now(UTC)
            active = await self._promotion_repo.list_active_for_storefront(
                store_id, now
            )
            linked = next((p for p in active if p.coupon_id == coupon.id), None)
            if linked is not None:
                targets = await self._target_repo.list_for_promotion(linked.id)
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

        # Increment the coupon's usage atomically (legacy behavior).
        await self._coupon_repo.increment_usage(coupon.id)

        # Record the redemption event when there is a linked promotion.
        if linked_promo_id is not None:
            # piasters / cents — `discount_amount` is in EGP at this point.
            cents = int((discount_amount * Decimal("100")).to_integral_value())
            await self._event_repo.record(
                PromotionEvent(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    promotion_id=linked_promo_id,
                    event_type="redeem",
                    discount_amount_cents=cents,
                    customer_id=visitor.customer_id if visitor else None,
                    session_id=visitor.visitor_token if visitor else None,
                )
            )

        return ApplyCouponV2Output(
            coupon_id=coupon.id,
            code=coupon.code,
            coupon_type=coupon.coupon_type.value,
            discount_amount=discount_amount,
            free_shipping=free_shipping,
            promotion_id=linked_promo_id,
        )
