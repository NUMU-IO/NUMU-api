"""Refund routes nested under stores/orders.

URL: /stores/{store_id}/orders/{order_id}/refunds
     /stores/{store_id}/refunds (store-level list)
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from src.api.dependencies import (
    get_order_repository,
    get_store_repository,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.repositories import (
    get_network_reputation_repository,
    get_refund_repository,
)
from src.api.dependencies.services import get_payment_service_for_provider

logger = logging.getLogger(__name__)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.common import PaginatedListResponse
from src.api.v1.schemas.tenant.refund import (
    CreateRefundRequest,
    RefundListItemResponse,
    RefundResponse,
    RejectRefundRequest,
)
from src.application.dto.refund import CreateRefundDTO
from src.application.use_cases.refunds import (
    ApproveRefundUseCase,
    CreateRefundUseCase,
    GetRefundUseCase,
    ListRefundsUseCase,
    ProcessRefundUseCase,
    RejectRefundUseCase,
)
from src.infrastructure.repositories import (
    OrderRepository,
    RefundRepository,
    StoreRepository,
)

router = APIRouter()


# --- Helpers ---


def _refund_to_response(dto) -> RefundResponse:
    """Convert RefundDTO to RefundResponse."""
    return RefundResponse(
        id=str(dto.id),
        order_id=str(dto.order_id),
        store_id=str(dto.store_id),
        refund_number=dto.refund_number,
        refund_type=dto.refund_type,
        status=dto.status,
        reason=dto.reason,
        reason_note=dto.reason_note,
        amount=dto.amount,
        currency=dto.currency,
        payment_provider=dto.payment_provider,
        payment_id=dto.payment_id,
        provider_refund_id=dto.provider_refund_id,
        requested_by=str(dto.requested_by) if dto.requested_by else None,
        approved_by=str(dto.approved_by) if dto.approved_by else None,
        rejected_by=str(dto.rejected_by) if dto.rejected_by else None,
        processed_at=dto.processed_at,
        completed_at=dto.completed_at,
        rejected_at=dto.rejected_at,
        failure_reason=dto.failure_reason,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
    )


def _refund_list_item_to_response(dto) -> RefundListItemResponse:
    """Convert RefundListItemDTO to RefundListItemResponse."""
    return RefundListItemResponse(
        id=str(dto.id),
        refund_number=dto.refund_number,
        order_id=str(dto.order_id),
        order_number=dto.order_number,
        refund_type=dto.refund_type,
        status=dto.status,
        reason=dto.reason,
        amount=dto.amount,
        currency=dto.currency,
        created_at=dto.created_at,
    )


# --- Order-scoped refund routes ---


@router.post(
    "/{store_id}/orders/{order_id}/refunds",
    response_model=SuccessResponse[RefundResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Request a refund for an order",
    operation_id="create_refund",
)
async def create_refund(
    store_id: Annotated[UUID, Path(description="Store ID")],
    order_id: Annotated[UUID, Path(description="Order ID")],
    request: CreateRefundRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    refund_repo: Annotated[RefundRepository, Depends(get_refund_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Create a refund request for an order."""
    use_case = CreateRefundUseCase(refund_repo, order_repo, store_repo)
    dto = CreateRefundDTO(
        order_id=order_id,
        refund_type=request.refund_type,
        reason=request.reason,
        reason_note=request.reason_note,
        amount=request.amount,
    )
    result = await use_case.execute(dto, store_id, user_id)
    return SuccessResponse(
        data=_refund_to_response(result),
        message="Refund request created successfully",
    )


@router.get(
    "/{store_id}/orders/{order_id}/refunds",
    response_model=SuccessResponse[PaginatedListResponse[RefundListItemResponse]],
    summary="List refunds for an order",
    operation_id="list_order_refunds",
)
async def list_order_refunds(
    store_id: Annotated[UUID, Path(description="Store ID")],
    order_id: Annotated[UUID, Path(description="Order ID")],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    refund_repo: Annotated[RefundRepository, Depends(get_refund_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """List all refunds for a specific order."""
    use_case = ListRefundsUseCase(refund_repo, order_repo, store_repo)
    result = await use_case.execute(
        store_id=store_id,
        user_id=user_id,
        order_id=order_id,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse(
        data=PaginatedListResponse(
            items=[_refund_list_item_to_response(item) for item in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            total_pages=result.total_pages,
        ),
    )


@router.get(
    "/{store_id}/orders/{order_id}/refunds/{refund_id}",
    response_model=SuccessResponse[RefundResponse],
    summary="Get refund details",
    operation_id="get_refund",
)
async def get_refund(
    store_id: Annotated[UUID, Path(description="Store ID")],
    order_id: Annotated[UUID, Path(description="Order ID")],
    refund_id: Annotated[UUID, Path(description="Refund ID")],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    refund_repo: Annotated[RefundRepository, Depends(get_refund_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Get details of a specific refund."""
    use_case = GetRefundUseCase(refund_repo, store_repo)
    result = await use_case.execute(refund_id, store_id, user_id)
    return SuccessResponse(data=_refund_to_response(result))


@router.post(
    "/{store_id}/orders/{order_id}/refunds/{refund_id}/approve",
    response_model=SuccessResponse[RefundResponse],
    summary="Approve a refund request",
    operation_id="approve_refund",
)
async def approve_refund(
    store_id: Annotated[UUID, Path(description="Store ID")],
    order_id: Annotated[UUID, Path(description="Order ID")],
    refund_id: Annotated[UUID, Path(description="Refund ID")],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    refund_repo: Annotated[RefundRepository, Depends(get_refund_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Approve a pending refund request."""
    use_case = ApproveRefundUseCase(refund_repo, store_repo)
    result = await use_case.execute(refund_id, store_id, user_id)
    return SuccessResponse(
        data=_refund_to_response(result),
        message="Refund approved successfully",
    )


@router.post(
    "/{store_id}/orders/{order_id}/refunds/{refund_id}/reject",
    response_model=SuccessResponse[RefundResponse],
    summary="Reject a refund request",
    operation_id="reject_refund",
)
async def reject_refund(
    store_id: Annotated[UUID, Path(description="Store ID")],
    order_id: Annotated[UUID, Path(description="Order ID")],
    refund_id: Annotated[UUID, Path(description="Refund ID")],
    request: RejectRefundRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    refund_repo: Annotated[RefundRepository, Depends(get_refund_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Reject a pending refund request."""
    use_case = RejectRefundUseCase(refund_repo, store_repo)
    result = await use_case.execute(refund_id, store_id, user_id, request.reason)
    return SuccessResponse(
        data=_refund_to_response(result),
        message="Refund rejected",
    )


@router.post(
    "/{store_id}/orders/{order_id}/refunds/{refund_id}/process",
    response_model=SuccessResponse[RefundResponse],
    summary="Process a refund through payment provider",
    operation_id="process_refund",
)
async def process_refund(
    store_id: Annotated[UUID, Path(description="Store ID")],
    order_id: Annotated[UUID, Path(description="Order ID")],
    refund_id: Annotated[UUID, Path(description="Refund ID")],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    refund_repo: Annotated[RefundRepository, Depends(get_refund_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    network_repo: Annotated[object, Depends(get_network_reputation_repository)],
):
    """Process an approved refund through the payment provider."""
    use_case = ProcessRefundUseCase(refund_repo, order_repo, store_repo)

    # Resolve the payment service for this refund
    refund = await refund_repo.get_by_id(refund_id)
    payment_service = None
    if refund and refund.payment_provider:
        try:
            payment_service = get_payment_service_for_provider(refund.payment_provider)
        except ValueError:
            pass  # Will fall through to manual processing

    result = await use_case.execute(refund_id, store_id, user_id, payment_service)

    # ── Network reputation: record the refund event ──────────────────
    # Mirrors the Shopify webhook path (refunds increment total_refunds).
    # Idempotent: a flag is written to order.metadata so retries / replays
    # don't double-count. Fail-open — never raises into the refund flow.
    if result.status == "completed":
        try:
            order = await order_repo.get_by_id(order_id)
            if order and order.shipping_address and order.shipping_address.phone:
                metadata = order.metadata or {}
                if not metadata.get("network_refund_recorded"):
                    from src.application.services.network_reputation_service import (
                        extract_phone_hash_from_string,
                        write_network_event,
                    )

                    phone_hash = extract_phone_hash_from_string(
                        order.shipping_address.phone
                    )
                    if phone_hash:
                        await write_network_event(
                            phone_hash=phone_hash,
                            store_id=store_id,
                            event_type="refund",
                            network_repo=network_repo,
                        )
                        order.metadata = {**metadata, "network_refund_recorded": True}
                        await order_repo.update(order)
                        logger.info(
                            "network_refund_recorded order=%s store=%s",
                            str(order_id),
                            str(store_id),
                        )
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning("network_refund_record_failed: %s", exc)

    return SuccessResponse(
        data=_refund_to_response(result),
        message="Refund processed successfully"
        if result.status == "completed"
        else "Refund processing failed",
    )


# --- Store-level refund list ---


@router.get(
    "/{store_id}/refunds",
    response_model=SuccessResponse[PaginatedListResponse[RefundListItemResponse]],
    summary="List all refunds for a store",
    operation_id="list_store_refunds",
)
async def list_store_refunds(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    refund_repo: Annotated[RefundRepository, Depends(get_refund_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    status_filter: str | None = Query(
        None, alias="status", description="Filter by refund status"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """List all refunds for a store with optional status filter."""
    use_case = ListRefundsUseCase(refund_repo, order_repo, store_repo)
    result = await use_case.execute(
        store_id=store_id,
        user_id=user_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse(
        data=PaginatedListResponse(
            items=[_refund_list_item_to_response(item) for item in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            total_pages=result.total_pages,
        ),
    )
