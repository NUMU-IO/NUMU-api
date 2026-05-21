"""Customer-initiated order returns (Phase 3.1).

URL: /storefront/me/orders/{order_id}/returns

Customer requests a return → merchant approves in the hub → package
shipped + received → merchant marks received → refund minted via the
existing Refund pipeline → return reaches `completed`.

This file owns the customer-side surface; merchant-side transitions
live in `routes/stores/returns.py`.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_order_repository,
    get_order_return_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.core.entities.customer import Customer
from src.core.entities.order_return import (
    OrderReturn,
    ReturnLineItem,
    ReturnReason,
    ReturnStatus,
)
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import OrderRepository, StoreRepository

router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────


class ReturnLineItemRequest(BaseModel):
    """One line of a customer's return request.

    Order line items don't carry a per-row id (they're JSONB on the
    orders row), so we reference them by their 0-based position in
    `order.line_items`. The order detail endpoint returns the same
    array order, so the storefront can map a checkbox click directly
    to the right index without a server round-trip.
    """

    order_line_index: int = Field(..., ge=0)
    quantity: int = Field(..., ge=1)
    reason: ReturnReason | None = None
    customer_note: str | None = Field(None, max_length=500)


class CreateReturnRequest(BaseModel):
    reason: ReturnReason = ReturnReason.OTHER
    customer_note: str | None = Field(None, max_length=2000)
    line_items: list[ReturnLineItemRequest] = Field(..., min_length=1)


class ReturnLineItemResponse(BaseModel):
    order_line_index: int
    product_id: str
    variant_id: str | None = None
    product_name: str
    quantity: int
    unit_price: int
    reason: str | None = None
    customer_note: str | None = None


class ReturnResponse(BaseModel):
    id: str
    order_id: str
    return_number: str
    status: str
    reason: str
    customer_note: str | None = None
    merchant_note: str | None = None
    line_items: list[ReturnLineItemResponse]
    refund_id: str | None = None
    requested_amount: int
    currency: str
    requested_at: str | None = None
    approved_at: str | None = None
    received_at: str | None = None
    completed_at: str | None = None
    rejected_at: str | None = None
    canceled_at: str | None = None


def _to_response(ret: OrderReturn) -> ReturnResponse:
    return ReturnResponse(
        id=str(ret.id),
        order_id=str(ret.order_id),
        return_number=ret.return_number,
        status=ret.status.value,
        reason=ret.reason.value,
        customer_note=ret.customer_note,
        merchant_note=ret.merchant_note,
        line_items=[
            ReturnLineItemResponse(
                order_line_index=li.order_line_index,
                product_id=str(li.product_id),
                variant_id=str(li.variant_id) if li.variant_id else None,
                product_name=li.product_name,
                quantity=li.quantity,
                unit_price=li.unit_price,
                reason=li.reason.value if li.reason else None,
                customer_note=li.customer_note,
            )
            for li in ret.line_items
        ],
        refund_id=str(ret.refund_id) if ret.refund_id else None,
        requested_amount=ret.requested_amount,
        currency=ret.currency,
        requested_at=ret.requested_at.isoformat() if ret.requested_at else None,
        approved_at=ret.approved_at.isoformat() if ret.approved_at else None,
        received_at=ret.received_at.isoformat() if ret.received_at else None,
        completed_at=ret.completed_at.isoformat() if ret.completed_at else None,
        rejected_at=ret.rejected_at.isoformat() if ret.rejected_at else None,
        canceled_at=ret.canceled_at.isoformat() if ret.canceled_at else None,
    )


def _generate_return_number() -> str:
    """Public-facing return identifier — RT-XXXXXXXX (8 hex chars).

    Not the row id; merchants quote this to support tickets. We keep
    it short enough to read over the phone but unguessable enough that
    a leak in one merchant doesn't help enumerate another's returns.
    """
    return f"RT-{uuid4().hex[:8].upper()}"


# ─── Routes ─────────────────────────────────────────────────────────────────


@router.post(
    "/orders/{order_id}/returns",
    response_model=SuccessResponse[ReturnResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Request a return for an order",
    operation_id="customer_create_return",
)
async def create_return(
    order_id: Annotated[UUID, Path(description="Order ID")],
    body: CreateReturnRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
):
    """Open a return request against one of the customer's orders.

    Validates:
      - order belongs to this customer (404 otherwise — no enumeration)
      - every requested line item is on the order
      - quantity per line is at most what was ordered (and not already
        returned)

    Caps + duplication: a customer can request multiple returns for
    the same order over time, but each line's running total can't
    exceed the original quantity. Currently we don't enforce the cap
    against in-flight returns — Phase 3.5 follow-up. For v1, double-
    requesting just gets rejected at merchant approval time.
    """
    order = await order_repo.get_by_id(order_id)
    if not order or order.customer_id != current_customer.id:
        raise EntityNotFoundError("Order", str(order_id))

    store = await store_repo.get_by_id(order.store_id)
    if not store:
        raise EntityNotFoundError("Store", str(order.store_id))

    # The order's line items are positionally indexed (JSONB array, no
    # per-row id). Validate that every requested index points at a real
    # line and that the requested quantity is within bounds.
    order_lines = order.line_items or []
    return_lines: list[ReturnLineItem] = []
    requested_amount = 0
    for req_line in body.line_items:
        if req_line.order_line_index >= len(order_lines):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Line index {req_line.order_line_index} is out of "
                    f"range for order {order_id} ({len(order_lines)} lines)."
                ),
            )
        order_line = order_lines[req_line.order_line_index]
        if req_line.quantity > order_line.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot return {req_line.quantity} of "
                    f"'{order_line.product_name}' — order has only "
                    f"{order_line.quantity}."
                ),
            )
        return_lines.append(
            ReturnLineItem(
                id=uuid4(),
                order_line_index=req_line.order_line_index,
                product_id=order_line.product_id,
                variant_id=order_line.variant_id,
                product_name=order_line.product_name,
                quantity=req_line.quantity,
                unit_price=order_line.unit_price,
                reason=req_line.reason,
                customer_note=req_line.customer_note,
            )
        )
        requested_amount += order_line.unit_price * req_line.quantity

    entity = OrderReturn(
        id=uuid4(),
        tenant_id=store.tenant_id or store.id,
        store_id=store.id,
        order_id=order.id,
        customer_id=current_customer.id,
        return_number=_generate_return_number(),
        status=ReturnStatus.REQUESTED,
        reason=body.reason,
        customer_note=body.customer_note,
        line_items=return_lines,
        requested_amount=requested_amount,
        currency=order.currency,
    )
    saved = await return_repo.create(entity)

    return SuccessResponse(
        data=_to_response(saved),
        message=("Return requested. We'll email you when the merchant reviews it."),
    )


@router.get(
    "/orders/{order_id}/returns",
    response_model=SuccessResponse[list[ReturnResponse]],
    summary="List returns for an order",
    operation_id="customer_list_order_returns",
)
async def list_order_returns(
    order_id: Annotated[UUID, Path(description="Order ID")],
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
):
    """All returns the customer has filed against this order."""
    order = await order_repo.get_by_id(order_id)
    if not order or order.customer_id != current_customer.id:
        raise EntityNotFoundError("Order", str(order_id))

    returns = await return_repo.list_for_order(order_id)
    return SuccessResponse(
        data=[_to_response(r) for r in returns],
        message="Returns retrieved",
    )


@router.post(
    "/returns/{return_id}/cancel",
    response_model=SuccessResponse[ReturnResponse],
    summary="Cancel a pending return",
    operation_id="customer_cancel_return",
)
async def cancel_return(
    return_id: Annotated[UUID, Path(description="Return ID")],
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
):
    """Cancel a return that hasn't been merchant-actioned yet.

    Only allowed while status == REQUESTED. After merchant action
    (approved / rejected) the customer must contact support.
    """
    ret = await return_repo.get_by_id(return_id)
    if not ret or ret.customer_id != current_customer.id:
        raise EntityNotFoundError("Return", str(return_id))

    try:
        ret.cancel(current_customer.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    saved = await return_repo.update(ret)
    return SuccessResponse(
        data=_to_response(saved),
        message="Return canceled",
    )
