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

import hashlib
import secrets
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_current_user_id,
    get_order_activity_repository,
    get_order_repository,
    verify_store_ownership,
)
from src.api.dependencies.database import get_db
from src.api.dependencies.services import get_storage_service
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_image_upload
from src.application.use_cases.payments.review_payment_proof import (
    ReviewDecision,
    ReviewPaymentProofUseCase,
)
from src.core.entities.instapay import (
    ManualPaymentIntentStatus,
    PaymentProof,
    PaymentProofStatus,
)
from src.core.entities.order import OrderStatus, PaymentStatus
from src.core.entities.order_activity import OrderActivity, OrderActivityKind
from src.core.entities.store import Store
from src.core.interfaces.services.storage_service import (
    IStorageService,
    StorageBucket,
)
from src.core.logging import get_logger
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.payment_proof import (
    PaymentProofModel,
)
from src.infrastructure.external_services.image.proof_sanitizer import (
    ProofImageDecodeError,
    sanitize_proof_image,
)
from src.infrastructure.external_services.manual_transfer import (
    MANUAL_TRANSFER_METHODS,
)
from src.infrastructure.repositories.instapay_intent_repository import (
    ManualPaymentIntentRepository,
)
from src.infrastructure.repositories.order_activity_repository import (
    OrderActivityRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.payment_proof_repository import (
    PaymentProofRepository,
)

log = get_logger(__name__)

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
    # NULL once the retention sweeper has purged the R2 object (see
    # ``instapay_expiry_task``). The row — and the amount on it — survives;
    # only the image is gone, and the hub renders a "removed after 90 days"
    # state instead of a broken image with a Refresh button that can never
    # succeed.
    signed_image_url: str | None
    created_at: datetime
    # Rail the merchant picked when recording this payment by hand from the
    # order page. NULL on customer-submitted proofs.
    recorded_method: str | None = None
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
    # An empty key means the retention sweeper deleted the object and
    # nulled the key. Composing a URL for it would send the browser after
    # bytes that no longer exist.
    image_url = (
        f"/api/v1/stores/{proof.store_id}/payment-proofs/{proof.id}/image"
        if proof.proof_image_key
        else None
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
        signed_image_url=image_url,
        created_at=proof.created_at,
        recorded_method=proof.recorded_method,
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
    intent_repo = ManualPaymentIntentRepository(db)
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
    intent_repo = ManualPaymentIntentRepository(db)
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
    summary="List manual-payment orders awaiting merchant review",
)
async def list_pending_instapay_orders(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> SuccessResponse[PendingVerificationPage]:
    """List manual-rail orders whose latest proof is awaiting merchant review.

    Covers InstaPay and Vodafone Cash. Path keeps its historical name so
    the hub's existing call site is untouched.

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
            # Both manual rails land here — a Vodafone Cash order whose
            # proof is awaiting review is the same merchant task as an
            # InstaPay one, and filtering on "instapay" alone would have
            # made those orders invisible in the hub.
            OrderModel.payment_method.in_(sorted(MANUAL_TRANSFER_METHODS)),
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


# ── Merchant-recorded payments (order page) ───────────────────────────
#
# A merchant who takes an order by hand — over WhatsApp, Instagram DM or
# a phone call — often collects part of the money up front on Vodafone
# Cash or InstaPay and receives a screenshot of the receipt. These two
# routes let them record that against the order: attach the receipt, type
# the amount actually paid, and let the order show "paid X, remaining Y"
# until it settles.
#
# Each recorded payment is a ``payment_proofs`` row, which is why this
# lives here rather than in ``orders.py``. Two deliberate properties:
#
#   * The row is written APPROVED, never AUTO_APPROVED. The daily
#     auto-approval caps read ``daily_auto_approve_stats``, which filters
#     on AUTO_APPROVED — so merchant rows can never eat the store's budget
#     and soft-block a real customer proof later the same day.
#   * No OCR runs on this path. The image is a record for the merchant,
#     not an input to any calculation, so every ``ocr_*`` column stays
#     NULL and the customer-facing vision pipeline is untouched.
#
# Nothing here writes to ``payment_transactions``. A partial row there
# would be picked up by the reconciliation run — whose order set is
# filtered to ``payment_status == "paid"`` — and reported back to the
# merchant as a ``transaction_no_order`` mismatch.

# Rail -> the name a merchant would use for it. The values land in the order
# timeline, which is merchant-facing text, so the raw enum must not leak there.
RECORDED_PAYMENT_METHOD_NAMES: dict[str, str] = {
    "vodafone_cash": "Vodafone Cash",
    "instapay": "InstaPay",
    "cash": "Cash",
    "bank_transfer": "Bank transfer",
    "other": "Other",
}

RECORDED_PAYMENT_METHODS: frozenset[str] = frozenset(RECORDED_PAYMENT_METHOD_NAMES)

# ``declared_amount_cents`` is a PostgreSQL ``integer``. Anything past this
# would fail at INSERT with a driver error and surface as a 500, so we
# reject it up front as the validation error it actually is.
_MAX_AMOUNT_CENTS = 2_147_483_647


def _bilingual(message: str, message_ar: str) -> dict[str, str]:
    """Error detail the hub can show in either language.

    ``api-error.ts`` reads ``message`` / ``message_ar`` off an object detail
    and prefers the Arabic the server wrote over anything it could infer from
    the English string. These messages are merchant-facing, and the merchant
    reading them is usually Egyptian — so they are written, not translated.
    """
    return {"message": message, "message_ar": message_ar}


class RecordedPaymentResult(BaseModel):
    """What the order page needs to re-render after recording a payment."""

    payment: PaymentProofResponse
    amount_paid_cents: int
    balance_due_cents: int
    order_payment_status: str


class VoidPaymentRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


async def _payment_totals(
    proof_repo: PaymentProofRepository,
    order,
) -> tuple[int, int]:
    """Return ``(amount_paid_cents, balance_due_cents)`` for an order.

    ``collectible_total`` rather than ``total`` so an order that went
    through partial acceptance at the door is measured against what the
    merchant should actually receive, not the original basket.
    """
    paid = await proof_repo.amount_paid_cents(order.id)
    return paid, max(0, order.collectible_total - paid)


@router.post(
    "/orders/{order_id}/payments",
    operation_id="merchant_record_order_payment",
    response_model=SuccessResponse[RecordedPaymentResult],
    status_code=status.HTTP_201_CREATED,
    summary="Record an out-of-band payment against an order",
)
async def record_order_payment(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_id: Annotated[UUID, Path()],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    activity_repo: Annotated[
        OrderActivityRepository, Depends(get_order_activity_repository)
    ],
    image: Annotated[UploadFile, File(description="Receipt screenshot")],
    amount_cents: Annotated[int, Form(gt=0, le=_MAX_AMOUNT_CENTS)],
    method: Annotated[str, Form()],
    reference: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Form()] = None,
) -> SuccessResponse[RecordedPaymentResult]:
    """Attach a receipt and the amount paid; the balance follows.

    The amount is the merchant's, not the image's — nothing reads the
    screenshot. When the running total reaches the order's collectible
    total, the order is marked paid through the same helper the
    "Mark as paid" button uses.
    """
    if method not in RECORDED_PAYMENT_METHODS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_bilingual(
                "Unknown payment method. Expected one of: "
                f"{', '.join(sorted(RECORDED_PAYMENT_METHODS))}.",
                "طريقة دفع مش معروفة.",
            ),
        )

    proof_repo = PaymentProofRepository(db)

    order = await order_repo.get_by_id(order_id)
    if order is None or order.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )

    # Idempotency is checked after the order is loaded but BEFORE the
    # paid / closed guards below. A retry that arrives after the first
    # request settled the order must return the original payment, not the
    # "already fully paid" 409 — otherwise a dropped connection looks like
    # a failure and the merchant records the money a second time.
    #
    # The key is unique per store, not per order, so a replay carrying a key
    # that belongs to a different order is a client mistake: answering it
    # with that other order's totals would be worse than refusing.
    if idempotency_key:
        existing = await proof_repo.get_by_idempotency_key(store.id, idempotency_key)
        if existing is not None:
            if existing.order_id != order.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_bilingual(
                        "This idempotency key was already used on another order.",
                        "المفتاح ده اتستخدم قبل كده على طلب تاني.",
                    ),
                )
            paid, balance = await _payment_totals(proof_repo, order)
            return SuccessResponse(
                data=RecordedPaymentResult(
                    payment=await _hydrate_proof(existing, storage_service),
                    amount_paid_cents=paid,
                    balance_due_cents=balance,
                    order_payment_status=order.payment_status.value,
                ),
                message="Payment already recorded",
            )

    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_bilingual(
                "This order is already fully paid.",
                "الطلب ده مدفوع بالكامل خلاص.",
            ),
        )
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_bilingual(
                "This order is closed. Payments cannot be recorded against it.",
                "الطلب ده مقفول، مش ممكن تسجّل عليه دفعات.",
            ),
        )

    raw_bytes = await validate_image_upload(image)
    try:
        # Strips EXIF, downscales, re-encodes, and hands back the pHash in
        # the same decode pass — so the stored bytes, the SHA-256 dedup key
        # and the perceptual hash all describe the same image.
        sanitized = sanitize_proof_image(
            raw_bytes,
            content_type=image.content_type or "application/octet-stream",
        )
    except ProofImageDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Could not decode the uploaded image: {exc}",
        ) from exc

    # A merchant-typed reference is the rail's own transaction number. When
    # they have none to hand we mint a distinguishable placeholder, so the
    # NOT NULL + per-store unique constraint on the column still holds and a
    # generated key can never collide with a real reference.
    transaction_ref = (reference or "").strip() or f"MAN-{secrets.token_hex(4).upper()}"

    image_hash = hashlib.sha256(sanitized.bytes).digest()
    if await proof_repo.image_hash_exists(store.id, image_hash):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_bilingual(
                "This receipt has already been recorded in your store.",
                "الصورة دي اتسجلت قبل كده في المتجر.",
            ),
        )
    if await proof_repo.transaction_ref_exists(store.id, transaction_ref):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_bilingual(
                "This transaction reference has already been used.",
                "رقم العملية ده اتستخدم قبل كده.",
            ),
        )

    uploaded = await storage_service.upload_file(
        file_content=sanitized.bytes,
        filename=f"{store.id}/{order_id}/{transaction_ref}.bin",
        content_type=sanitized.content_type or "application/octet-stream",
        bucket=StorageBucket.PAYMENT_PROOFS,
    )

    proof = PaymentProof.new(
        tenant_id=order.tenant_id,
        store_id=store.id,
        order_id=order.id,
        proof_image_key=uploaded.key,
        proof_image_hash=image_hash,
        transaction_ref=transaction_ref,
        declared_amount_cents=amount_cents,
        idempotency_key=idempotency_key,
        recorded_method=method,
        perceptual_hash=sanitized.perceptual_hash,
    )
    # The merchant IS the reviewer here — they are looking at the receipt as
    # they type the amount. APPROVED (not AUTO_APPROVED) keeps this row out
    # of the auto-approval daily caps; see the module note above.
    proof.mark_approved(user_id)

    try:
        created = await proof_repo.create(proof)
    except Exception:
        # Don't leave the object orphaned in R2 when the row didn't land.
        try:
            await storage_service.delete_file(uploaded.key)
        except Exception:
            log.warning("recorded_payment_r2_cleanup_failed", key=uploaded.key)
        raise

    paid, balance = await _payment_totals(proof_repo, order)

    if balance == 0:
        from src.api.v1.routes.stores.orders import apply_manual_payment

        order = await apply_manual_payment(order, order_repo)
        # A storefront order can still have a live manual-payment intent
        # sitting in the merchant's review queue. The money is in, so close
        # it rather than leaving it to expire.
        intent_repo = ManualPaymentIntentRepository(db)
        intent = await intent_repo.get_by_order_id(order.id)
        if intent is not None and intent.status in (
            ManualPaymentIntentStatus.AWAITING_PAYMENT,
            ManualPaymentIntentStatus.PROOF_RECEIVED,
        ):
            await intent_repo.update_status(intent.id, ManualPaymentIntentStatus.PAID)

    await activity_repo.create(
        OrderActivity(
            order_id=order.id,
            store_id=store.id,
            tenant_id=order.tenant_id,
            user_id=user_id,
            kind=OrderActivityKind.SYSTEM_EVENT,
            event_type="payment_recorded",
            body=(
                f"Recorded {amount_cents / 100:,.2f} {order.currency} "
                f"via {RECORDED_PAYMENT_METHOD_NAMES[method]}"
            ),
            metadata={
                "amount_cents": amount_cents,
                "method": method,
                "transaction_ref": transaction_ref,
                "proof_id": str(created.id),
                "amount_paid_cents": paid,
                "balance_due_cents": balance,
            },
        )
    )

    return SuccessResponse(
        data=RecordedPaymentResult(
            payment=await _hydrate_proof(created, storage_service),
            amount_paid_cents=paid,
            balance_due_cents=balance,
            order_payment_status=order.payment_status.value,
        ),
        message="Payment recorded",
    )


@router.post(
    "/payments/{proof_id}/void",
    operation_id="merchant_void_recorded_payment",
    response_model=SuccessResponse[RecordedPaymentResult],
    summary="Void a payment the merchant recorded by hand",
)
async def void_recorded_payment(
    store: Annotated[Store, Depends(verify_store_ownership)],
    proof_id: Annotated[UUID, Path()],
    body: VoidPaymentRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    activity_repo: Annotated[
        OrderActivityRepository, Depends(get_order_activity_repository)
    ],
) -> SuccessResponse[RecordedPaymentResult]:
    """Undo a mistyped amount. The balance goes back up by that amount.

    Refused on a paid order: un-marking the payment is a separate,
    deliberate action with its own commission and invoice reversal, so the
    merchant does that first and then voids. That one rule keeps this
    endpoint free of any cascade logic.
    """
    proof_repo = PaymentProofRepository(db)
    proof = await proof_repo.get_by_id(proof_id)
    if proof is None or proof.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found."
        )
    if proof.recorded_method is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_bilingual(
                "This is a customer-submitted proof, not a recorded payment. "
                "Reject it from the review panel instead.",
                "ده إثبات من العميل، مش دفعة إنت سجّلتها. ارفضه من لوحة المراجعة.",
            ),
        )
    if proof.status != PaymentProofStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_bilingual(
                "This payment has already been voided.",
                "الدفعة دي اتلغت قبل كده.",
            ),
        )

    order = await order_repo.get_by_id(proof.order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )
    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_bilingual(
                "Unmark the order as paid first, then void the payment.",
                'شيل علامة "مدفوع" عن الطلب الأول، وبعدين ألغي الدفعة.',
            ),
        )

    proof.mark_rejected(user_id, body.reason)
    updated = await proof_repo.update(proof)

    # Re-read the order after the void lands: a concurrent request may have
    # marked it PAID in between, and answering with the row we loaded at the
    # top would show the hub a stale status next to fresh money totals.
    order = await order_repo.get_by_id(proof.order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )

    paid, balance = await _payment_totals(proof_repo, order)

    await activity_repo.create(
        OrderActivity(
            order_id=order.id,
            store_id=store.id,
            tenant_id=order.tenant_id,
            user_id=user_id,
            kind=OrderActivityKind.SYSTEM_EVENT,
            event_type="payment_voided",
            body=(
                f"Voided {(proof.declared_amount_cents or 0) / 100:,.2f} "
                f"{order.currency}: {body.reason}"
            ),
            metadata={
                "amount_cents": proof.declared_amount_cents,
                "proof_id": str(proof.id),
                "reason": body.reason,
                "amount_paid_cents": paid,
                "balance_due_cents": balance,
            },
        )
    )

    return SuccessResponse(
        data=RecordedPaymentResult(
            payment=await _hydrate_proof(updated, storage_service),
            amount_paid_cents=paid,
            balance_due_cents=balance,
            order_payment_status=order.payment_status.value,
        ),
        message="Payment voided",
    )
