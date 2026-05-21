"""Merchant-side return management (Phase 3.1).

URL: /stores/{store_id}/returns/...

Hub UI calls these to approve/reject pending returns, mark received
once the package physically arrives, and trigger the refund step.

The refund step bridges into the existing Refund pipeline rather than
re-implementing payment-provider integration here — see the
`process_return_refund` route below.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import (
    get_current_user_id,
    verify_store_ownership,
)
from src.api.dependencies.repositories import (
    get_order_return_repository,
    get_refund_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.storefront.returns import ReturnResponse, _to_response
from src.core.entities.order_return import (
    OrderReturn,
    ReturnStatus,
)
from src.core.entities.refund import (
    Refund,
    RefundReason,
    RefundStatus,
    RefundType,
)
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import RefundRepository, StoreRepository

router = APIRouter(
    prefix="/{store_id}/returns",
    tags=["Returns"],
    dependencies=[Depends(verify_store_ownership)],
)


# ─── Schemas ─────────────────────────────────────────────────────────────────


class ApproveReturnRequest(BaseModel):
    merchant_note: str | None = Field(None, max_length=2000)


class RejectReturnRequest(BaseModel):
    merchant_note: str = Field(..., min_length=1, max_length=2000)


class MarkReceivedRequest(BaseModel):
    merchant_note: str | None = Field(None, max_length=2000)


class IssueRefundRequest(BaseModel):
    """Optional override of the refund amount (defaults to requested_amount)."""

    amount: int | None = Field(
        None,
        ge=0,
        description=(
            "Refund amount in cents. When omitted, defaults to the "
            "return's requested_amount (sum of line item totals)."
        ),
    )
    reason_note: str | None = Field(None, max_length=2000)


# ─── Routes ─────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[list[ReturnResponse]],
    summary="List returns for the store",
    operation_id="merchant_list_returns",
)
async def list_returns(
    store_id: Annotated[UUID, Path(description="Store ID")],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
    return_status: ReturnStatus | None = Query(
        None,
        alias="status",
        description="Filter by status (e.g. requested, approved, received).",
    ),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Hub list view — newest first."""
    rows = await return_repo.list_for_store(
        store_id=store_id,
        status=return_status,
        limit=limit,
        offset=offset,
    )
    return SuccessResponse(
        data=[_to_response(r) for r in rows],
        message="Returns retrieved",
    )


@router.get(
    "/{return_id}",
    response_model=SuccessResponse[ReturnResponse],
    summary="Get one return",
    operation_id="merchant_get_return",
)
async def get_return(
    store_id: Annotated[UUID, Path(description="Store ID")],
    return_id: Annotated[UUID, Path(description="Return ID")],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
):
    ret = await _load_return(return_id, store_id, return_repo)
    return SuccessResponse(data=_to_response(ret), message="Return retrieved")


@router.post(
    "/{return_id}/approve",
    response_model=SuccessResponse[ReturnResponse],
    summary="Approve a pending return",
    operation_id="merchant_approve_return",
)
async def approve_return(
    store_id: Annotated[UUID, Path(description="Store ID")],
    return_id: Annotated[UUID, Path(description="Return ID")],
    body: ApproveReturnRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
):
    ret = await _load_return(return_id, store_id, return_repo)
    try:
        ret.approve(user_id, merchant_note=body.merchant_note)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    saved = await return_repo.update(ret)
    return SuccessResponse(data=_to_response(saved), message="Return approved")


@router.post(
    "/{return_id}/reject",
    response_model=SuccessResponse[ReturnResponse],
    summary="Reject a return",
    operation_id="merchant_reject_return",
)
async def reject_return(
    store_id: Annotated[UUID, Path(description="Store ID")],
    return_id: Annotated[UUID, Path(description="Return ID")],
    body: RejectReturnRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
):
    ret = await _load_return(return_id, store_id, return_repo)
    try:
        ret.reject(user_id, reason=body.merchant_note)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    saved = await return_repo.update(ret)
    return SuccessResponse(data=_to_response(saved), message="Return rejected")


@router.post(
    "/{return_id}/mark-received",
    response_model=SuccessResponse[ReturnResponse],
    summary="Mark a return as physically received",
    operation_id="merchant_mark_return_received",
)
async def mark_return_received(
    store_id: Annotated[UUID, Path(description="Store ID")],
    return_id: Annotated[UUID, Path(description="Return ID")],
    body: MarkReceivedRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
):
    """Set status from `approved` → `received`. Required before
    issuing the refund — receiving is the merchant's signal that the
    package physically arrived in acceptable condition."""
    ret = await _load_return(return_id, store_id, return_repo)
    try:
        ret.mark_received(user_id, note=body.merchant_note)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    saved = await return_repo.update(ret)
    return SuccessResponse(data=_to_response(saved), message="Return marked received")


@router.post(
    "/{return_id}/refund",
    response_model=SuccessResponse[ReturnResponse],
    summary="Issue refund for a received return",
    operation_id="merchant_issue_return_refund",
)
async def issue_return_refund(
    store_id: Annotated[UUID, Path(description="Store ID")],
    return_id: Annotated[UUID, Path(description="Return ID")],
    body: IssueRefundRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    return_repo: Annotated[Any, Depends(get_order_return_repository)],
    refund_repo: Annotated[RefundRepository, Depends(get_refund_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Mint a Refund row linked to this return + transition the return.

    Bridges into the existing Refund state machine rather than
    re-implementing payment-provider integration. The refund row's
    metadata.return_id field links the two; once the refund completes
    via its own pipeline, a future webhook will transition the return
    to `completed`. For this endpoint we just mint + link.

    Why we don't kick off provider processing synchronously:
        Provider refund APIs (Paymob, Fawry, Stripe) are slow, flaky,
        and asymmetric — some return immediately, some require polling.
        The existing Refund use cases handle the dispatch + retry
        pipeline. We just hand off here.
    """
    ret = await _load_return(return_id, store_id, return_repo)
    if ret.status != ReturnStatus.RECEIVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Refunds can only be issued after the return is marked received."),
        )
    if ret.refund_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A refund has already been minted for this return.",
        )

    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    amount = body.amount if body.amount is not None else ret.requested_amount
    if amount > ret.requested_amount:
        # Merchants can refund LESS than requested (partial refund of a
        # partial return) but not more — that would let a return become
        # an arbitrary store-credit dispenser.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Refund amount {amount} exceeds the return's "
                f"requested_amount {ret.requested_amount}."
            ),
        )

    refund = Refund(
        id=uuid4(),
        order_id=ret.order_id,
        store_id=ret.store_id,
        tenant_id=ret.tenant_id,
        refund_number=f"RF-{uuid4().hex[:8].upper()}",
        refund_type=(
            RefundType.FULL if amount == ret.requested_amount else RefundType.PARTIAL
        ),
        status=RefundStatus.REQUESTED,
        reason=RefundReason.CUSTOMER_REQUEST,
        reason_note=body.reason_note or ret.customer_note,
        amount=amount,
        currency=ret.currency,
        requested_by=user_id,
        # Link both ways: the refund's metadata carries the return id
        # so the existing refund-update webhook handler can transition
        # the return to `completed` once the gateway succeeds.
        metadata={"return_id": str(ret.id), "return_number": ret.return_number},
    )
    saved_refund = await refund_repo.create(refund)

    ret.refund_id = saved_refund.id
    saved_ret = await return_repo.update(ret)

    return SuccessResponse(
        data=_to_response(saved_ret),
        message=(
            "Refund minted. The payment provider will be charged via the "
            "refund pipeline; track its status from the refunds page."
        ),
    )


# ─── Helpers ────────────────────────────────────────────────────────────────


async def _load_return(
    return_id: UUID, store_id: UUID, return_repo: Any
) -> OrderReturn:
    """Load a return + verify store ownership.

    Returns 404 (not 403) on cross-store probes — same enumeration-
    safe pattern as the storefront orders route.
    """
    ret = await return_repo.get_by_id(return_id)
    if not ret or ret.store_id != store_id:
        raise EntityNotFoundError("Return", str(return_id))
    return ret
