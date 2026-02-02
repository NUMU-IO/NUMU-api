"""Coupon routes nested under stores.

URL: /stores/{store_id}/coupons
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from src.api.dependencies import (
    get_coupon_repository,
    get_store_repository,
    require_store_owner,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    CouponResponse,
    CreateCouponRequest,
    DeleteResponse,
    PaginatedListResponse,
    UpdateCouponRequest,
)
from src.application.dto.coupon import CreateCouponDTO, UpdateCouponDTO
from src.application.use_cases.coupons import (
    CreateCouponUseCase,
    DeleteCouponUseCase,
    GetCouponUseCase,
    ListCouponsUseCase,
    UpdateCouponUseCase,
)
from src.infrastructure.repositories import CouponRepository, StoreRepository

router = APIRouter(prefix="/{store_id}/coupons")


def _coupon_response(result) -> CouponResponse:
    """Convert CouponDTO to CouponResponse."""
    return CouponResponse(
        id=str(result.id),
        store_id=str(result.store_id),
        code=result.code,
        description=result.description,
        discount_type=result.discount_type,
        discount_value=result.discount_value,
        min_order_amount=result.min_order_amount,
        max_discount_amount=result.max_discount_amount,
        max_uses=result.max_uses,
        max_uses_per_customer=result.max_uses_per_customer,
        current_usage_count=result.current_usage_count,
        valid_from=str(result.valid_from) if result.valid_from else None,
        valid_to=str(result.valid_to) if result.valid_to else None,
        is_active=result.is_active,
        created_at=str(result.created_at),
        updated_at=str(result.updated_at),
    )


@router.post(
    "/",
    response_model=SuccessResponse[CouponResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new coupon",
)
async def create_coupon(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CreateCouponRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Create a new coupon for the store."""
    use_case = CreateCouponUseCase(
        coupon_repository=coupon_repo,
        store_repository=store_repo,
    )

    dto = CreateCouponDTO(
        code=request.code,
        description=request.description,
        discount_type=request.discount_type,
        discount_value=request.discount_value,
        min_order_amount=request.min_order_amount,
        max_discount_amount=request.max_discount_amount,
        max_uses=request.max_uses,
        max_uses_per_customer=request.max_uses_per_customer,
        valid_from=request.valid_from,
        valid_to=request.valid_to,
        is_active=request.is_active,
    )

    result = await use_case.execute(dto=dto, store_id=store_id, user_id=user_id)

    return SuccessResponse(
        data=_coupon_response(result),
        message="Coupon created successfully",
    )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[CouponResponse]],
    summary="List coupons",
)
async def list_coupons(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    is_active: bool | None = Query(None),
):
    """List coupons for a store."""
    use_case = ListCouponsUseCase(coupon_repository=coupon_repo)
    skip = (page - 1) * limit

    result = await use_case.execute(
        store_id=store_id,
        skip=skip,
        limit=limit,
        is_active=is_active,
    )

    coupons = [_coupon_response(coupon) for coupon in result.items]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=coupons,
            total=result.total,
            page=page,
            page_size=limit,
            total_pages=result.total_pages,
        ),
        message="Coupons retrieved successfully",
    )


@router.get(
    "/{coupon_id}",
    response_model=SuccessResponse[CouponResponse],
    summary="Get coupon by ID",
)
async def get_coupon(
    store_id: Annotated[UUID, Path(description="Store ID")],
    coupon_id: Annotated[UUID, Path(description="Coupon ID")],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
):
    """Get coupon details by ID."""
    use_case = GetCouponUseCase(coupon_repository=coupon_repo)
    result = await use_case.execute(coupon_id=coupon_id)

    return SuccessResponse(
        data=_coupon_response(result),
        message="Coupon retrieved successfully",
    )


@router.patch(
    "/{coupon_id}",
    response_model=SuccessResponse[CouponResponse],
    summary="Update coupon",
)
async def update_coupon(
    store_id: Annotated[UUID, Path(description="Store ID")],
    coupon_id: Annotated[UUID, Path(description="Coupon ID")],
    request: UpdateCouponRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update coupon details."""
    use_case = UpdateCouponUseCase(
        coupon_repository=coupon_repo,
        store_repository=store_repo,
    )

    dto = UpdateCouponDTO(
        description=request.description,
        discount_value=request.discount_value,
        min_order_amount=request.min_order_amount,
        max_discount_amount=request.max_discount_amount,
        max_uses=request.max_uses,
        max_uses_per_customer=request.max_uses_per_customer,
        valid_from=request.valid_from,
        valid_to=request.valid_to,
        is_active=request.is_active,
    )

    result = await use_case.execute(coupon_id=coupon_id, dto=dto, user_id=user_id)

    return SuccessResponse(
        data=_coupon_response(result),
        message="Coupon updated successfully",
    )


@router.delete(
    "/{coupon_id}",
    response_model=SuccessResponse[DeleteResponse],
    summary="Delete coupon",
)
async def delete_coupon(
    store_id: Annotated[UUID, Path(description="Store ID")],
    coupon_id: Annotated[UUID, Path(description="Coupon ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Delete a coupon."""
    use_case = DeleteCouponUseCase(
        coupon_repository=coupon_repo,
        store_repository=store_repo,
    )

    await use_case.execute(coupon_id=coupon_id, user_id=user_id)

    return SuccessResponse(
        data=DeleteResponse(deleted=True, id=str(coupon_id)),
        message="Coupon deleted successfully",
    )
