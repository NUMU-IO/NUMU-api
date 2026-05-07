"""CalculateCartDiscountsUseCase — single source of truth for discount totals.

Called by both the cart endpoint AND the checkout submit. Composes
auto-discounts + the optional code-based discount via the domain
`DiscountCalculator`. Never touches the database after the initial
promotion fetch — the calculator is pure.
"""

from datetime import UTC, datetime
from uuid import UUID

from src.application.dto.promotion_resolution import (
    CartDiscountsOutput,
    VisitorContextInput,
)
from src.core.entities.cart import Cart
from src.core.entities.promotion import Promotion
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionRepository,
    IPromotionTargetRepository,
)
from src.core.services.discount_calculator import DiscountCalculator
from src.core.services.promotion_eligibility_checker import (
    EligibilityContext,
    PromotionEligibilityChecker,
)
from src.core.value_objects.discount_rule import CartLine, DiscountContext


class CalculateCartDiscountsUseCase:
    """Recompute the cart's discount given current promos + applied codes."""

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        target_repo: IPromotionTargetRepository,
        coupon_repo: ICouponRepository,
        eligibility_checker: PromotionEligibilityChecker,
        calculator: DiscountCalculator,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._target_repo = target_repo
        self._coupon_repo = coupon_repo
        self._checker = eligibility_checker
        self._calculator = calculator

    async def execute(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        cart: Cart,
        applied_coupon_codes: list[str],
        visitor: VisitorContextInput,
    ) -> CartDiscountsOutput:
        now = datetime.now(UTC)
        all_active = await self._promotion_repo.list_active_for_storefront(
            store_id, now
        )
        # Filter to (a) automatic promos passing eligibility, (b) the
        # specific code-based promos the customer typed in.
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

        eligible: list[Promotion] = []
        applied_codes_lower = {c.strip().upper() for c in applied_coupon_codes}
        for promo in all_active:
            if promo.status != PromotionStatus.ACTIVE:
                continue
            targets = await self._target_repo.list_for_promotion(promo.id)
            verdict = self._checker.is_eligible(promo, targets, ctx, now=now)
            if not verdict.eligible:
                continue
            if promo.surface == PromotionSurface.AUTOMATIC:
                eligible.append(promo)
            elif promo.surface == PromotionSurface.DISCOUNT_CODE:
                if promo.coupon_id is None:
                    continue
                coupon = await self._coupon_repo.get_by_id(promo.coupon_id)
                if coupon and coupon.code.upper() in applied_codes_lower:
                    eligible.append(promo)

        # Build the calculator context from the cart.
        line_items = [
            CartLine(
                product_id=ci.product_id,
                quantity=ci.quantity,
                unit_price_cents=ci.unit_price,
            )
            for ci in cart.items
        ]
        subtotal = sum(li.unit_price_cents * li.quantity for li in line_items)
        calc_ctx = DiscountContext(
            subtotal_cents=subtotal,
            line_items=line_items,
            customer_id=cart.customer_id,
        )
        result = self._calculator.calculate_total(
            eligible, list(applied_coupon_codes), calc_ctx
        )

        return CartDiscountsOutput(
            code_discount_cents=result.code_discount_cents,
            automatic_discount_cents=result.automatic_discount_cents,
            free_shipping=result.free_shipping,
            applied_promotion_ids=result.applied_promotion_ids,
            rejected=[
                {"promotion_id": str(pid), "reason": reason}
                for pid, reason in result.rejected
            ],
        )
