"""Customer-facing proof-upload endpoint for InstaPay orders.

URL: ``POST /storefront/store/{store_id}/orders/{order_id}/payment-proof``

The customer (a) pays via their bank app to the merchant's IPA, then (b)
returns to the storefront and uploads a screenshot of the transfer
receipt plus the bank-issued transaction reference. The route:

  1. Validates the upload (magic bytes, size) — reusing the same helper
     as product image uploads so we don't diverge on accepted formats.
  2. Resolves the merchant's InstaPay auto-approval config from
     ``store.settings``.
  3. Delegates to :class:`SubmitPaymentProofUseCase`, which handles
     dedup, storage, auto-rules, and the order-paid transition.

Returns the proof id + current status (``auto_approved`` /
``awaiting_review`` / ``rejected``) plus a short reason list when auto
review declines — so the storefront can show the right copy without a
second round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_optional_customer
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_order_repository,
    get_store_repository,
)
from src.api.dependencies.services import get_storage_service
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_image_upload
from src.application.use_cases.payments.submit_payment_proof import (
    SubmitPaymentProofUseCase,
)
from src.config import settings
from src.core.entities.customer import Customer
from src.core.interfaces.services.storage_service import IStorageService
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
)
from src.infrastructure.external_services.instapay.payment_service import (
    DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
    DEFAULT_AUTO_APPROVE_DAILY_COUNT,
    DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
)
from src.infrastructure.repositories.instapay_intent_repository import (
    InstapayIntentRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.payment_proof_repository import (
    PaymentProofRepository,
)
from src.infrastructure.repositories.store_repository import StoreRepository

router = APIRouter()


# ── Rate limit: proof uploads ────────────────────────────────────────
#
# Per (customer, order) — the narrower the key, the less a single bad
# actor can disrupt other customers. 5 attempts in 10 minutes gives the
# customer headroom for a photo retry or two without being punitive;
# sustained re-submission past that gets a 429 rather than a 409 spam
# trail in the dedup tables.
_PROOF_UPLOAD_MAX = 5
_PROOF_UPLOAD_WINDOW_SECONDS = 600

_cache: RedisCacheService | None = None


def _get_cache() -> RedisCacheService | None:
    """Return the Redis service, or None when Redis isn't configured.

    A misconfigured Redis shouldn't brick a legitimate upload — we log
    and fail open, matching the global rate-limit middleware's policy.
    """
    global _cache
    if _cache is None and settings.redis_host:
        _cache = RedisCacheService()
    return _cache


async def _enforce_upload_rate_limit(
    *, store_id: UUID, order_id: UUID, customer_id: UUID
) -> None:
    cache = _get_cache()
    if cache is None:
        return
    key = f"instapay_upload:{store_id}:{order_id}:{customer_id}"
    try:
        count = await cache.increment(key)
        if count == 1:
            # First hit in the window — attach the TTL. Subsequent INCRs
            # leave the TTL alone so a burst ages out together.
            client = await cache._get_client()
            await client.expire(key, _PROOF_UPLOAD_WINDOW_SECONDS)
        if count > _PROOF_UPLOAD_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Too many upload attempts for this order. "
                    "Please wait a few minutes before trying again."
                ),
            )
    except HTTPException:
        raise
    except Exception:
        # Redis is best-effort for rate limiting — don't block uploads
        # if it's flaky. The server-side dedup constraints still prevent
        # actual duplicate rows.
        return


class SubmitProofResponse(BaseModel):
    proof_id: UUID
    order_id: UUID
    status: str
    can_retry: bool
    reasons: list[str] = []
    signed_image_url: str
    expires_at: datetime


@router.post(
    "/orders/{order_id}/payment-proof",
    operation_id="storefront_submit_instapay_proof",
    response_model=SuccessResponse[SubmitProofResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Submit InstaPay payment proof",
)
async def submit_instapay_proof(
    store_id: Annotated[UUID, Path()],
    order_id: Annotated[UUID, Path()],
    transaction_ref: Annotated[str, Form(min_length=3, max_length=64)],
    file: Annotated[UploadFile, File(description="Payment screenshot")],
    optional_customer: Annotated[Customer | None, Depends(get_optional_customer)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_service: Annotated[IStorageService, Depends(get_storage_service)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    declared_amount_cents: Annotated[int | None, Form()] = None,
    idempotency_key: Annotated[str | None, Form(max_length=80)] = None,
    reference: Annotated[
        str | None,
        Form(
            max_length=32,
            description=(
                "Intent reference_code — required for guest checkouts to "
                "authorize the upload without a customer session."
            ),
        ),
    ] = None,
) -> SuccessResponse[SubmitProofResponse]:
    # Resolve the order + intent up front to authorize the request and
    # — for guests — to recover the customer_id the use-case needs.
    order_for_auth = await order_repo.get_by_id(order_id)
    if order_for_auth is None or order_for_auth.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )
    auth_intent_repo = InstapayIntentRepository(db)
    auth_intent = await auth_intent_repo.get_by_order_id(order_id)
    if auth_intent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No InstaPay intent exists for this order.",
        )
    customer_match = bool(
        optional_customer and order_for_auth.customer_id == optional_customer.id
    )
    reference_match = bool(reference and reference == auth_intent.reference_code)
    if not (customer_match or reference_match):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("Sign in or include the reference code to upload a proof."),
        )
    # Rate-limit on the order_id when authorized as a guest — the
    # authenticated path keys on customer_id which gives stronger
    # per-user fairness; both paths bound abuse on a single order.
    rate_limit_customer_id = (
        optional_customer.id if optional_customer else order_for_auth.customer_id
    )
    await _enforce_upload_rate_limit(
        store_id=store_id,
        order_id=order_id,
        customer_id=rate_limit_customer_id,
    )

    image_bytes = await validate_image_upload(file)

    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found.",
        )

    # Resolve per-store auto-approval thresholds from settings (the same
    # JSONB block the checkout branch read). Module defaults apply when
    # the merchant left a field unset.
    payment_settings = (store.settings or {}).get("payment", {}).get("instapay", {})
    auto_config = AutoApprovalConfig(
        threshold_cents=int(
            payment_settings.get(
                "auto_approve_threshold_cents",
                DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
            )
        ),
        daily_cap_cents=int(
            payment_settings.get(
                "auto_approve_daily_cap_cents",
                DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
            )
        ),
        daily_count_cap=int(
            payment_settings.get(
                "auto_approve_daily_count",
                DEFAULT_AUTO_APPROVE_DAILY_COUNT,
            )
        ),
    )

    intent_repo = InstapayIntentRepository(db)
    proof_repo = PaymentProofRepository(db)
    use_case = SubmitPaymentProofUseCase(
        session=db,
        order_repo=order_repo,
        intent_repo=intent_repo,
        proof_repo=proof_repo,
        storage_service=storage_service,
    )

    # Guest path: pass the order's own customer_id so the use case's
    # ownership check still passes. Customer record exists on every
    # order (guest checkouts synthesize one) so this is always set.
    use_case_customer_id = (
        optional_customer.id if optional_customer else order_for_auth.customer_id
    )
    result = await use_case.execute(
        store_id=store_id,
        order_id=order_id,
        customer_id=use_case_customer_id,
        image_bytes=image_bytes,
        image_content_type=file.content_type or "application/octet-stream",
        transaction_ref=transaction_ref,
        declared_amount_cents=declared_amount_cents,
        idempotency_key=idempotency_key,
        auto_approval_config=auto_config,
    )

    # The intent's expires_at is the effective retry deadline for the
    # customer — past it they can no longer re-upload, so the storefront
    # should stop the countdown timer.
    return SuccessResponse(
        data=SubmitProofResponse(
            proof_id=result.proof.id,
            order_id=result.order.id,
            status=result.proof.status.value,
            can_retry=result.proof.can_retry,
            reasons=result.decision.reasons,
            signed_image_url=result.signed_image_url,
            expires_at=result.intent.expires_at,
        )
    )


class InstapayStatusResponse(BaseModel):
    order_id: UUID
    reference_code: str
    ipa: str
    ipa_display_name: str | None = None
    fallback_phone: str | None = None
    amount_cents: int
    currency: str
    expires_at: datetime
    expires_in_seconds: int
    intent_status: str
    payment_status: str
    latest_proof: dict | None = None
    # Public URL of the merchant's uploaded InstaPay QR image (taken
    # from inside their InstaPay app). The page falls back to showing
    # the IPA + reference when this is null.
    qr_image_url: str | None = None
    # InstaPay "Share link" URL — when set, the storefront renders a
    # QR code client-side from this string instead of showing the
    # uploaded image.
    qr_link_url: str | None = None


@router.get(
    "/orders/{order_id}/instapay-status",
    operation_id="storefront_get_instapay_status",
    response_model=SuccessResponse[InstapayStatusResponse],
    summary="Get current InstaPay intent + latest proof status",
)
async def get_instapay_status(
    store_id: Annotated[UUID, Path()],
    order_id: Annotated[UUID, Path()],
    optional_customer: Annotated[Customer | None, Depends(get_optional_customer)],
    db: Annotated[AsyncSession, Depends(get_db)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    reference: Annotated[
        str | None,
        Query(
            description=(
                "InstaPay reference_code (returned in checkout payment_data) — "
                "lets guest checkouts read their own status without a customer "
                "session. Logged-in customers can omit it; the customer_id "
                "match is checked first."
            ),
            max_length=32,
        ),
    ] = None,
) -> SuccessResponse[InstapayStatusResponse]:
    order = await order_repo.get_by_id(order_id)
    if order is None or order.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )

    intent_repo = InstapayIntentRepository(db)
    proof_repo = PaymentProofRepository(db)

    intent = await intent_repo.get_by_order_id(order_id)
    if intent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No InstaPay intent exists for this order.",
        )

    # Authorize: either the request is from the order's owning customer,
    # OR it carries the intent's reference_code (which the customer
    # received in the checkout response and is the storefront's normal
    # way to land on the InstaPay page). The reference_code's
    # ~10⁹-combination namespace plus its TTL keep guessing attacks
    # impractical, and the only data exposed here is non-sensitive
    # (IPA shown openly, masked phone, status flags).
    customer_match = bool(
        optional_customer and order.customer_id == optional_customer.id
    )
    reference_match = bool(reference and reference == intent.reference_code)
    if not (customer_match or reference_match):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Sign in or pass the reference code to view this order's "
                "InstaPay status."
            ),
        )

    latest = await proof_repo.get_latest_for_order(order_id)
    latest_dict = None
    if latest is not None:
        latest_dict = {
            "id": str(latest.id),
            "status": latest.status.value,
            "transaction_ref": latest.transaction_ref,
            "rejection_reason": latest.rejection_reason,
            "can_retry": latest.can_retry,
            "created_at": latest.created_at.isoformat(),
        }

    now = datetime.now(UTC)
    expires_in = max(0, int((intent.expires_at - now).total_seconds()))

    # Pull qr_image_url + display name from store.settings — the
    # intent row doesn't carry them. One extra cached lookup is fine
    # here; the page only polls every 20s.
    qr_image_url: str | None = None
    qr_link_url: str | None = None
    ipa_display_name: str | None = None
    try:
        store = await store_repo.get_by_id(store_id)
        if store is not None:
            instapay_settings = (
                (store.settings or {}).get("payment", {}).get("instapay", {})
            )
            qr_image_url = instapay_settings.get("qr_image_url")
            qr_link_url = instapay_settings.get("qr_link_url")
            ipa_display_name = instapay_settings.get("ipa_display_name")
    except Exception:
        # Non-fatal: the page still works without the QR/display name.
        pass

    return SuccessResponse(
        data=InstapayStatusResponse(
            order_id=order.id,
            reference_code=intent.reference_code,
            ipa=intent.display_ipa,
            ipa_display_name=ipa_display_name,
            fallback_phone=intent.display_phone,
            amount_cents=intent.amount_cents,
            currency=order.currency,
            expires_at=intent.expires_at,
            expires_in_seconds=expires_in,
            intent_status=intent.status.value,
            payment_status=order.payment_status.value,
            latest_proof=latest_dict,
            qr_image_url=qr_image_url,
            qr_link_url=qr_link_url,
        )
    )
