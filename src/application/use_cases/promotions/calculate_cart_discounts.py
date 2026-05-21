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
from src.core.interfaces.repositories.promotion_event_repository import (
    IPromotionEventRepository,
)
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
        event_repo: IPromotionEventRepository | None = None,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._target_repo = target_repo
        self._coupon_repo = coupon_repo
        self._checker = eligibility_checker
        self._calculator = calculator
        # Optional — when set, the use case enforces per-promotion
        # `usage_limit_total` caps by querying the convert event count
        # for any promotion that has a non-null limit configured. When
        # `None`, the checker still receives convert_count_total=0 and
        # the cap is never reached, which keeps existing test seams
        # (no need to back-fill the dependency in mock-heavy tests).
        self._event_repo = event_repo

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
        # Stash targets per eligible promotion so the calculator can
        # build BOGO buy/get-set filters without re-fetching. The
        # eligibility check already loaded these — don't pay for the
        # round-trip twice.
        targets_by_promotion: dict[UUID, list] = {}
        applied_codes_lower = {c.strip().upper() for c in applied_coupon_codes}
        for promo in all_active:
            if promo.status != PromotionStatus.ACTIVE:
                continue
            targets = await self._target_repo.list_for_promotion(promo.id)

            # Per-promotion usage caps need an event-count lookup.
            # Only query for promos that actually have a cap set —
            # the common case (no cap) keeps a single repo call.
            convert_count_total = 0
            if self._event_repo is not None and promo.usage_limit_total is not None:
                counts = await self._event_repo.counts_for_promotion(promo.id)
                convert_count_total = counts.conversions
            scoped_ctx = (
                ctx
                if convert_count_total == 0
                else EligibilityContext(
                    customer_id=ctx.customer_id,
                    customer_tags=ctx.customer_tags,
                    cart_subtotal_cents=ctx.cart_subtotal_cents,
                    cart_product_ids=ctx.cart_product_ids,
                    cart_category_ids=ctx.cart_category_ids,
                    country=ctx.country,
                    city=ctx.city,
                    device=ctx.device,
                    is_first_visit=ctx.is_first_visit,
                    is_logged_in=ctx.is_logged_in,
                    dismissed_promotion_ids=ctx.dismissed_promotion_ids,
                    convert_count_total=convert_count_total,
                )
            )

            verdict = self._checker.is_eligible(promo, targets, scoped_ctx, now=now)
            if not verdict.eligible:
                continue
            if promo.surface == PromotionSurface.AUTOMATIC:
                eligible.append(promo)
                targets_by_promotion[promo.id] = targets
            elif promo.surface == PromotionSurface.DISCOUNT_CODE:
                if promo.coupon_id is None:
                    continue
                coupon = await self._coupon_repo.get_by_id(promo.coupon_id)
                if coupon and coupon.code.upper() in applied_codes_lower:
                    eligible.append(promo)
                    targets_by_promotion[promo.id] = targets

        # Build the calculator context from the cart. `category_id` is
        # optional — only populated when the storefront sends it (the
        # cart-discounts endpoint passes it through; the order-create
        # path may not, in which case category-scoped BOGO rules just
        # won't match). That degrades quietly rather than 500-ing.
        line_items = [
            CartLine(
                product_id=ci.product_id,
                quantity=ci.quantity,
                unit_price_cents=ci.unit_price,
                category_id=ci.category_id,
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
            eligible,
            list(applied_coupon_codes),
            calc_ctx,
            targets_by_promotion=targets_by_promotion,
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
