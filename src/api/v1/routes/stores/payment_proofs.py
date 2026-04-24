"""Merchant-facing proof-review endpoints for InstaPay orders.

URLs:
  - ``GET  /stores/{store_id}/orders/{order_id}/payment-proofs``
      List every proof ever uploaded for an order (re-upload history).
  - ``POST /stores/{store_id}/payment-proofs/{proof_id}/approve``
  - ``POST /stores/{store_id}/payment-proofs/{proof_id}/reject``
      Flip a queued proof into APPROVED or REJECTED; approval also
      pushes the order into PAID and fires ``OrderPaidEvent`` so the
      usual downstream (invoice, email, shipment) fans out.

These routes do not include the InstaPay checkout flow — that sits in
:mod:`src.api.v1.routes.storefront.checkout`. They exist only for the
asynchronous review step the merchant performs *after* the customer
has submitted a proof.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_order_repository,
    verify_store_ownership,
)
from src.api.dependencies.database import get_db
from src.api.dependencies.services import get_storage_service
from src.api.responses import SuccessResponse
from src.application.use_cases.payments.review_payment_proof import (
    ReviewDecision,
    ReviewPaymentProofUseCase,
)
from src.core.entities.instapay import PaymentProofStatus
from src.core.entities.store import Store
from src.core.interfaces.services.storage_service import IStorageService
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.payment_proof import (
    PaymentProofModel,
)
from src.infrastructure.repositories.instapay_intent_repository import (
    InstapayIntentRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.payment_proof_repository import (
    PaymentProofRepository,
)

router = APIRouter(prefix="/{store_id}")


# ── Response models ───────────────────────────────────────────────────


class PaymentProofResponse(BaseModel):
    id: UUID
    order_id: UUID
    transaction_ref: str
    declared_amount_cents: int | None
    status: str
    rejection_reason: str | None
    review_decision_by: UUID | None
    review_decision_at: datetime | None
    signed_image_url: str
    created_at: datetime


class RejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


# ── Helpers ──────────────────────────────────────────────────────────


async def _hydrate_proof(
    proof,
    storage_service: IStorageService,
) -> PaymentProofResponse:
    signed_url = await storage_service.get_signed_url(
        proof.proof_image_key, expires_in=3600
    )
    return PaymentProofResponse(
        id=proof.id,
        order_id=proof.order_id,
        transaction_ref=proof.transaction_ref,
        declared_amount_cents=proof.declared_amount_cents,
        status=proof.status.value,
        rejection_reason=proof.rejection_reason,
        review_decision_by=proof.review_decision_by,
        review_decision_at=proof.review_decision_at,
        signed_image_url=signed_url,
        created_at=proof.created_at,
    )


# ── Routes ───────────────────────────────────────────────────────────


@router.get(
    "/orders/{order_id}/payment-proofs",
    operation_id="merchant_list_payment_proofs",
    response_model=SuccessResponse[list[PaymentProofResponse]],
    summary="List payment proofs for an order",
)
async def list_payment_proofs(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_id: Annotated[UUID, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
) -> SuccessResponse[list[PaymentProofResponse]]:
    order = await order_repo.get_by_id(order_id)
    if order is None or order.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )

    proof_repo = PaymentProofRepository(db)
    proofs = await proof_repo.list_for_order(order_id)
    # Fan the signed-URL lookups in parallel — sequential awaits would
    # cost N serial S3 round-trips on a page that can show a small
    # history of re-upload attempts for a single order.
    data = await asyncio.gather(*[_hydrate_proof(p, storage_service) for p in proofs])
    return SuccessResponse(data=list(data))


@router.post(
    "/payment-proofs/{proof_id}/approve",
    operation_id="merchant_approve_payment_proof",
    response_model=SuccessResponse[PaymentProofResponse],
    summary="Approve a customer-submitted proof",
)
async def approve_payment_proof(
    store: Annotated[Store, Depends(verify_store_ownership)],
    proof_id: Annotated[UUID, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
) -> SuccessResponse[PaymentProofResponse]:
    intent_repo = InstapayIntentRepository(db)
    proof_repo = PaymentProofRepository(db)

    # Verify the proof actually belongs to this store (defence in depth —
    # RLS already narrows by tenant, this narrows by store).
    proof = await proof_repo.get_by_id(proof_id)
    if proof is None or proof.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment proof not found.",
        )

    use_case = ReviewPaymentProofUseCase(
        session=db,
        order_repo=order_repo,
        intent_repo=intent_repo,
        proof_repo=proof_repo,
    )
    result = await use_case.execute(
        proof_id=proof_id,
        reviewer_user_id=store.owner_id,
        decision=ReviewDecision.APPROVE,
    )
    return SuccessResponse(data=await _hydrate_proof(result.proof, storage_service))


@router.post(
    "/payment-proofs/{proof_id}/reject",
    operation_id="merchant_reject_payment_proof",
    response_model=SuccessResponse[PaymentProofResponse],
    summary="Reject a customer-submitted proof",
)
async def reject_payment_proof(
    store: Annotated[Store, Depends(verify_store_ownership)],
    proof_id: Annotated[UUID, Path()],
    body: RejectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
) -> SuccessResponse[PaymentProofResponse]:
    intent_repo = InstapayIntentRepository(db)
    proof_repo = PaymentProofRepository(db)

    proof = await proof_repo.get_by_id(proof_id)
    if proof is None or proof.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment proof not found.",
        )

    use_case = ReviewPaymentProofUseCase(
        session=db,
        order_repo=order_repo,
        intent_repo=intent_repo,
        proof_repo=proof_repo,
    )
    result = await use_case.execute(
        proof_id=proof_id,
        reviewer_user_id=store.owner_id,
        decision=ReviewDecision.REJECT,
        rejection_reason=body.reason,
    )
    return SuccessResponse(data=await _hydrate_proof(result.proof, storage_service))


# ── Pending-verification queue ────────────────────────────────────────


class PendingVerificationOrder(BaseModel):
    order_id: UUID
    order_number: str
    customer_id: UUID
    amount_cents: int
    currency: str
    created_at: datetime
    proof_id: UUID
    proof_created_at: datetime
    transaction_ref: str
    declared_amount_cents: int | None


class PendingVerificationPage(BaseModel):
    items: list[PendingVerificationOrder]
    total: int
    page: int
    limit: int


@router.get(
    "/orders/pending-instapay-review",
    operation_id="merchant_list_pending_instapay_orders",
    response_model=SuccessResponse[PendingVerificationPage],
    summary="List InstaPay orders awaiting merchant review",
)
async def list_pending_instapay_orders(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> SuccessResponse[PendingVerificationPage]:
    """List InstaPay orders whose most recent proof is awaiting merchant review.

    Powers the merchant hub's "Pending verification" filter chip. We
    self-join payment_proofs to find only the *latest* proof per order
    and filter on its status, so an old rejected proof followed by an
    approved one doesn't get surfaced here. Read-only; doesn't modify
    any state.
    """
    # Latest-proof-per-order subquery: max(created_at) grouped by order_id.
    latest_proof_per_order = (
        select(
            PaymentProofModel.order_id,
            func.max(PaymentProofModel.created_at).label("latest_at"),
        )
        .where(PaymentProofModel.store_id == store.id)
        .group_by(PaymentProofModel.order_id)
        .subquery()
    )

    base = (
        select(OrderModel, PaymentProofModel)
        .join(
            PaymentProofModel,
            PaymentProofModel.order_id == OrderModel.id,
        )
        .join(
            latest_proof_per_order,
            (latest_proof_per_order.c.order_id == PaymentProofModel.order_id)
            & (latest_proof_per_order.c.latest_at == PaymentProofModel.created_at),
        )
        .where(
            OrderModel.store_id == store.id,
            OrderModel.payment_method == "instapay",
            PaymentProofModel.status == PaymentProofStatus.AWAITING_REVIEW,
        )
    )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(PaymentProofModel.created_at.asc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()

    items = [
        PendingVerificationOrder(
            order_id=order.id,
            order_number=order.order_number,
            customer_id=order.customer_id,
            amount_cents=order.total,
            currency=order.currency,
            created_at=order.created_at,
            proof_id=proof.id,
            proof_created_at=proof.created_at,
            transaction_ref=proof.transaction_ref,
            declared_amount_cents=proof.declared_amount_cents,
        )
        for order, proof in rows
    ]

    return SuccessResponse(
        data=PendingVerificationPage(
            items=items, total=int(total), page=page, limit=limit
        )
    )
