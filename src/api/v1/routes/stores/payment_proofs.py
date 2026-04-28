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

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response
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
    # Phase C — OCR readout the merchant review pane renders next
    # to the proof image so the merchant can see why a soft-block
    # fired (or that the OCR engine was unavailable). All optional
    # — pre-Phase-C rows leave them null and the UI just hides.
    ocr_status: str | None = None
    ocr_provider: str | None = None
    ocr_extracted_amount_cents: int | None = None
    ocr_extracted_ipa: str | None = None
    ocr_extracted_note: str | None = None
    ocr_extracted_transaction_ref: str | None = None
    ocr_extracted_recipient_name: str | None = None
    # Phase D — rule-engine tags explaining why auto-approval didn't
    # fire (e.g. ``["ocr_amount_mismatch"]``). NULL when the proof was
    # auto-approved or the row predates the column.
    auto_approval_block_reasons: list[str] | None = None


class RejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


# ── Helpers ──────────────────────────────────────────────────────────


async def _hydrate_proof(
    proof,
    storage_service: IStorageService,
) -> PaymentProofResponse:
    # We used to return a presigned S3 URL here, but in containerised
    # deployments where MinIO sits behind a path-rewriting reverse
    # proxy the SigV4 signature fails validation (the signed canonical
    # path doesn't survive the rewrite). Instead, route image fetches
    # through the API itself — same-origin from the merchant hub means
    # the httpOnly auth cookie flows naturally, and we don't depend on
    # any storage hostname being browser-reachable.
    image_url = f"/api/v1/stores/{proof.store_id}/payment-proofs/{proof.id}/image"
    return PaymentProofResponse(
        id=proof.id,
        order_id=proof.order_id,
        transaction_ref=proof.transaction_ref,
        declared_amount_cents=proof.declared_amount_cents,
        status=proof.status.value,
        rejection_reason=proof.rejection_reason,
        review_decision_by=proof.review_decision_by,
        review_decision_at=proof.review_decision_at,
        signed_image_url=image_url,
        created_at=proof.created_at,
        ocr_status=proof.ocr_status,
        ocr_provider=proof.ocr_provider,
        ocr_extracted_amount_cents=proof.ocr_extracted_amount_cents,
        ocr_extracted_ipa=proof.ocr_extracted_ipa,
        ocr_extracted_note=proof.ocr_extracted_note,
        ocr_extracted_transaction_ref=proof.ocr_extracted_transaction_ref,
        ocr_extracted_recipient_name=proof.ocr_extracted_recipient_name,
        auto_approval_block_reasons=proof.auto_approval_block_reasons,
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
    # `_hydrate_proof` no longer hits storage (it just composes a URL),
    # so a plain comprehension replaces the earlier asyncio.gather.
    data = [await _hydrate_proof(p, storage_service) for p in proofs]
    return SuccessResponse(data=data)


@router.get(
    "/payment-proofs/{proof_id}/image",
    operation_id="merchant_stream_payment_proof_image",
    summary="Stream a payment proof image (merchant-authenticated)",
)
async def stream_payment_proof_image(
    store: Annotated[Store, Depends(verify_store_ownership)],
    proof_id: Annotated[UUID, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
):
    """Return the proof image bytes inline.

    This avoids handing the browser a presigned URL whose host the
    browser may not be able to reach (MinIO behind a path-rewriting
    proxy) and whose signature can't survive URI rewriting. The
    merchant is already authenticated via httpOnly cookie; we
    re-validate the proof belongs to this store and stream the bytes.

    A short private cache lets the merchant scroll back to a proof
    they just viewed without a fresh fetch, while keeping the bytes
    out of any shared cache.
    """
    proof_repo = PaymentProofRepository(db)
    proof = await proof_repo.get_by_id(proof_id)
    if proof is None or proof.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proof not found."
        )

    try:
        body, content_type = await storage_service.get_object_bytes(
            proof.proof_image_key
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proof image is missing."
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not fetch the proof image.",
        )

    # Fall back to a safe default — the route handler that wrote the
    # object stamps a real Content-Type, so this only matters in dev
    # against the local-disk backend.
    media_type = content_type or "application/octet-stream"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


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


# ── Reverse-image lookup (Phase B) ────────────────────────────────────


class SimilarProof(BaseModel):
    """One match on the per-store pHash neighbour scan.

    Mirrors the shape ``PaymentProofResponse`` exposes so the merchant
    review pane can reuse the same image-streaming path; the extra
    ``order_number`` + ``hamming_distance`` fields drive the
    "Possibly related submissions" UI.
    """

    proof_id: UUID
    order_id: UUID
    order_number: str
    status: str
    transaction_ref: str
    declared_amount_cents: int | None
    created_at: datetime
    signed_image_url: str
    hamming_distance: int


@router.get(
    "/payment-proofs/{proof_id}/similar",
    operation_id="merchant_list_similar_payment_proofs",
    response_model=SuccessResponse[list[SimilarProof]],
    summary="Find perceptually similar prior proofs in this store",
)
async def list_similar_payment_proofs(
    store: Annotated[Store, Depends(verify_store_ownership)],
    proof_id: Annotated[UUID, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[list[SimilarProof]]:
    """Surface prior proofs whose pHash is within Hamming distance ≤ 8.

    Looser than the dedup gate (≤ 5) — at review time the merchant
    benefits from seeing "even loosely similar" submissions, not just
    near-exact ones. Populates the "Possibly related submissions"
    panel above Approve/Reject so the merchant can spot e.g. the
    same screenshot resubmitted across two orders.

    Empty result when the proof has no perceptual_hash (predates
    Phase A) or when nothing similar exists in the per-store
    90-day window the repository scans.
    """
    from datetime import UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    proof_repo = PaymentProofRepository(db)
    proof = await proof_repo.get_by_id(proof_id)
    if proof is None or proof.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment proof not found.",
        )

    if proof.perceptual_hash is None:
        # Predates Phase A — no hash to compare. Return empty rather
        # than 404 so the UI panel just stays hidden.
        return SuccessResponse(data=[])

    neighbours = await proof_repo.find_perceptual_neighbours(
        store.id,
        proof.perceptual_hash,
        max_distance=8,
        since=_dt.now(UTC) - _td(days=90),
        limit=50,
    )
    # Drop the proof itself (distance 0) and cap to 10. Sort by
    # ascending distance so the most-similar match leads.
    filtered = sorted(
        [(p, d) for (p, d) in neighbours if p.id != proof.id],
        key=lambda pd: pd[1],
    )[:10]

    if not filtered:
        return SuccessResponse(data=[])

    # Batch-resolve order_numbers in one round-trip rather than N
    # repository hits. Read-only and store-scoped, so we hit the
    # OrderModel directly here.
    order_ids = list({p.order_id for (p, _) in filtered})
    rows = await db.execute(
        select(OrderModel.id, OrderModel.order_number).where(
            OrderModel.id.in_(order_ids),
            OrderModel.store_id == store.id,
        )
    )
    order_number_by_id = dict(rows.all())

    items: list[SimilarProof] = []
    for p, distance in filtered:
        order_number = order_number_by_id.get(p.order_id)
        if order_number is None:
            # Defensive — shouldn't happen given the join scope above,
            # but skip silently rather than error the whole panel.
            continue
        items.append(
            SimilarProof(
                proof_id=p.id,
                order_id=p.order_id,
                order_number=order_number,
                status=p.status.value,
                transaction_ref=p.transaction_ref,
                declared_amount_cents=p.declared_amount_cents,
                created_at=p.created_at,
                signed_image_url=(
                    f"/api/v1/stores/{p.store_id}/payment-proofs/{p.id}/image"
                ),
                hamming_distance=distance,
            )
        )

    return SuccessResponse(data=items)


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
