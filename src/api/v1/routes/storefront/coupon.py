"""Storefront coupon validation route.

URL: /storefront/store/{store_id}/coupons/apply

Allows authenticated customers to validate a coupon code
and see the calculated discount before checkout.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import get_coupon_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.storefront.coupon import ApplyCouponRequest, ApplyCouponResponse
from src.application.use_cases.coupons import ApplyCouponUseCase
from src.core.entities.customer import Customer
from src.infrastructure.repositories import CouponRepository

router = APIRouter()


@router.post(
    "/coupons/apply",
    response_model=SuccessResponse[ApplyCouponResponse],
    summary="Validate and apply coupon code",
)
async def apply_coupon(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: ApplyCouponRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
):
    """Validate a coupon code and return the calculated discount.

    Use this endpoint to check a coupon before submitting checkout.
    """
    use_case = ApplyCouponUseCase(coupon_repository=coupon_repo)

    result = await use_case.execute(
        store_id=store_id,
        code=request.coupon_code,
        order_amount=request.order_amount,
    )

    return SuccessResponse(
        data=ApplyCouponResponse(
            coupon_id=str(result.coupon_id),
            code=result.code,
            coupon_type=result.coupon_type,
            discount_amount=str(result.discount_amount),
            free_shipping=result.free_shipping,
        ),
        message="Coupon applied successfully",
    )
