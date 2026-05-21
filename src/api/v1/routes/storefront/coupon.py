"""Storefront coupon validation route.

URL: /storefront/store/{store_id}/coupons/apply

Allows authenticated customers to validate a coupon code and see the
calculated discount. Backed by `ApplyCouponV2UseCase` so the legacy
`/coupons/apply` flow now also honors the merchant's promotion-side
targeting rules (audience, geo, schedule, customer tags) when a
promotion is linked to the coupon. Coupons that aren't wrapped in a
promotion behave exactly as before — the V2 use case falls back to
the original Coupon-only flow when there's no link.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_coupon_repository,
    get_promotion_event_repository,
    get_promotion_repository,
    get_promotion_target_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.storefront.coupon import (
    ApplyCouponRequest,
    ApplyCouponResponse,
)
from src.application.dto.promotion_resolution import VisitorContextInput
from src.application.use_cases.promotions.apply_coupon_v2 import (
    ApplyCouponV2UseCase,
)
from src.core.entities.customer import Customer
from src.core.exceptions import EntityNotFoundError
from src.core.services.promotion_eligibility_checker import (
    PromotionEligibilityChecker,
)
from src.infrastructure.repositories import CouponRepository, StoreRepository
from src.infrastructure.repositories.promotion_event_repository import (
    PromotionEventRepository,
)
from src.infrastructure.repositories.promotion_repository import (
    PromotionRepository,
    PromotionTargetRepository,
)

router = APIRouter()

_VISITOR_COOKIE = "numu_visitor"


@router.post(
    "/coupons/apply",
    response_model=SuccessResponse[ApplyCouponResponse],
    summary="Validate and apply coupon code",
    operation_id="apply_coupon",
)
async def apply_coupon(
    request: Request,
    store_id: Annotated[UUID, Path(description="Store ID")],
    body: ApplyCouponRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
    event_repo: Annotated[
        PromotionEventRepository, Depends(get_promotion_event_repository)
    ],
):
    """Validate a coupon code and return the calculated discount.

    Use this endpoint to check a coupon before submitting checkout. If
    the coupon is wrapped in an active `discount_code` promotion, the
    promotion's targeting + scheduling rules are also enforced and a
    `redeem` event is recorded server-side for analytics.
    """
    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise EntityNotFoundError("Store", str(store_id))

    # Build the visitor context from what we know on this request. We
    # don't have cart line items here (legacy schema doesn't carry
    # them), so cart-product / cart-category targeting can't fire from
    # this endpoint — those rules are evaluated at the storefront's
    # `/promotions/active` resolve path. Audience / geo / customer-tag
    # targeting and the schedule window all work with what we have.
    visitor = VisitorContextInput(
        customer_id=current_customer.id,
        visitor_token=request.cookies.get(_VISITOR_COOKIE),
        is_logged_in=True,
        cart_subtotal_cents=int(body.order_amount * 100),
    )

    use_case = ApplyCouponV2UseCase(
        coupon_repo=coupon_repo,
        promotion_repo=promo_repo,
        target_repo=target_repo,
        event_repo=event_repo,
        eligibility_checker=PromotionEligibilityChecker(),
    )
    result = await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store_id,
        code=body.coupon_code,
        order_amount=body.order_amount,
        visitor=visitor,
    )

    return SuccessResponse(
        data=ApplyCouponResponse(
            coupon_id=str(result.coupon_id),
            code=result.code,
            coupon_type=result.coupon_type,
            discount_amount=str(result.discount_amount),
            free_shipping=result.free_shipping,
            promotion_id=str(result.promotion_id) if result.promotion_id else None,
        ),
        message="Coupon applied successfully",
    )
