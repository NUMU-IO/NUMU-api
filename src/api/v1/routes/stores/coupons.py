"""Coupon routes nested under stores.

URL: /stores/{store_id}/coupons
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from src.api.dependencies import (
    get_coupon_repository,
    get_store_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    CouponResponse,
    CreateCouponRequest,
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
from src.core.entities.store import Store
from src.infrastructure.repositories import CouponRepository, StoreRepository

router = APIRouter(prefix="/{store_id}/coupons")


def _coupon_response(result) -> CouponResponse:
    """Convert CouponDTO to CouponResponse."""
    return CouponResponse(
        id=str(result.id),
        store_id=str(result.store_id),
        code=result.code,
        coupon_type=result.coupon_type,
        value=str(result.value),
        min_order_amount=str(result.min_order_amount)
        if result.min_order_amount is not None
        else None,
        max_discount_amount=str(result.max_discount_amount)
        if result.max_discount_amount is not None
        else None,
        usage_limit=result.usage_limit,
        usage_count=result.usage_count,
        valid_from=str(result.valid_from) if result.valid_from else None,
        valid_until=str(result.valid_until) if result.valid_until else None,
        is_active=result.is_active,
        is_expired=result.is_expired,
        is_usable=result.is_usable,
        applicable_product_ids=[str(pid) for pid in result.applicable_product_ids]
        if result.applicable_product_ids
        else None,
        applicable_category_ids=[str(cid) for cid in result.applicable_category_ids]
        if result.applicable_category_ids
        else None,
        created_at=str(result.created_at),
        updated_at=str(result.updated_at),
    )


@router.post(
    "/",
    response_model=SuccessResponse[CouponResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new coupon",
    operation_id="create_coupon",
)
async def create_coupon(
    request: CreateCouponRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
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
        coupon_type=request.coupon_type,
        value=request.value,
        min_order_amount=request.min_order_amount,
        max_discount_amount=request.max_discount_amount,
        usage_limit=request.usage_limit,
        valid_from=request.valid_from,
        valid_until=request.valid_until,
        applicable_product_ids=[UUID(pid) for pid in request.applicable_product_ids]
        if request.applicable_product_ids
        else None,
        applicable_category_ids=[UUID(cid) for cid in request.applicable_category_ids]
        if request.applicable_category_ids
        else None,
    )

    result = await use_case.execute(dto=dto, store_id=store.id, user_id=store.owner_id)

    return SuccessResponse(
        data=_coupon_response(result),
        message="Coupon created successfully",
    )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[CouponResponse]],
    summary="List coupons",
    operation_id="list_coupons",
)
async def list_coupons(
    store: Annotated[Store, Depends(verify_store_ownership)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    is_active: bool | None = Query(None),
):
    """List coupons for a store."""
    use_case = ListCouponsUseCase(coupon_repository=coupon_repo)
    skip = (page - 1) * limit

    result = await use_case.execute(
        store_id=store.id,
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
    operation_id="get_coupon",
)
async def get_coupon(
    coupon_id: Annotated[UUID, Path(description="Coupon ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
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
    operation_id="update_coupon",
)
async def update_coupon(
    coupon_id: Annotated[UUID, Path(description="Coupon ID")],
    request: UpdateCouponRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update coupon details."""
    use_case = UpdateCouponUseCase(
        coupon_repository=coupon_repo,
        store_repository=store_repo,
    )

    dto = UpdateCouponDTO(
        code=request.code,
        coupon_type=request.coupon_type,
        value=request.value,
        min_order_amount=request.min_order_amount,
        max_discount_amount=request.max_discount_amount,
        usage_limit=request.usage_limit,
        valid_from=request.valid_from,
        valid_until=request.valid_until,
        is_active=request.is_active,
        applicable_product_ids=[UUID(pid) for pid in request.applicable_product_ids]
        if request.applicable_product_ids is not None
        else None,
        applicable_category_ids=[UUID(cid) for cid in request.applicable_category_ids]
        if request.applicable_category_ids is not None
        else None,
    )

    result = await use_case.execute(
        coupon_id=coupon_id, dto=dto, user_id=store.owner_id
    )

    return SuccessResponse(
        data=_coupon_response(result),
        message="Coupon updated successfully",
    )


@router.delete(
    "/{coupon_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete coupon",
    operation_id="delete_coupon",
)
async def delete_coupon(
    coupon_id: Annotated[UUID, Path(description="Coupon ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Delete a coupon."""
    use_case = DeleteCouponUseCase(
        coupon_repository=coupon_repo,
        store_repository=store_repo,
    )

    await use_case.execute(coupon_id=coupon_id, user_id=store.owner_id)

    return None
