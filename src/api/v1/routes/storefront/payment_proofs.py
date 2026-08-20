"""Customer-facing proof-upload endpoint for manual-rail orders.

URL: ``POST /storefront/store/{store_id}/orders/{order_id}/payment-proof``

Serves both manual rails — InstaPay and Vodafone Cash. The customer
(a) sends funds to the merchant's destination out-of-band (a bank app
for InstaPay; ``*9#`` or the Ana Vodafone app for Vodafone Cash), then
(b) returns to the storefront and uploads a screenshot of the receipt
plus the transaction reference. The route:

  1. Validates the upload (magic bytes, size) — reusing the same helper
     as product image uploads so we don't diverge on accepted formats.
  2. Reads the rail off the order's intent and resolves *that* rail's
     auto-approval config from ``store.settings`` — never a hardcoded
     ``["payment"]["instapay"]`` lookup, which would silently apply
     InstaPay's thresholds to a Vodafone Cash proof.
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
from src.api.dependencies.services import (
    get_proof_vision_service_for_store,
    get_storage_service,
)
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_image_upload
from src.application.use_cases.payments.submit_payment_proof import (
    SubmitPaymentProofUseCase,
)
from src.config import settings
from src.core.entities.customer import Customer
from src.core.entities.instapay import ManualPaymentMethod
from src.core.interfaces.services.storage_service import IStorageService
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.external_services.image.proof_sanitizer import (
    ProofImageDecodeError,
    sanitize_proof_image,
)
from src.infrastructure.external_services.manual_transfer import (
    DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
    DEFAULT_AUTO_APPROVE_DAILY_COUNT,
    DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
    AutoApprovalConfig,
    default_amount_tolerance_bps,
    default_auto_approve_enabled,
    get_merchant_manual_credentials,
)
from src.infrastructure.external_services.manual_transfer import (
    settings_key as manual_settings_key,
)
from src.infrastructure.repositories.instapay_intent_repository import (
    ManualPaymentIntentRepository,
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
    summary="Submit payment proof (InstaPay / Vodafone Cash)",
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
    auth_intent_repo = ManualPaymentIntentRepository(db)
    auth_intent = await auth_intent_repo.get_by_order_id(order_id)
    if auth_intent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No payment intent exists for this order.",
        )
    # The rail is a property of the intent, not of the request — a
    # customer can't talk us into scoring their proof against the
    # other rail's thresholds.
    manual_method = auth_intent.method
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

    raw_bytes = await validate_image_upload(file)

    # Phase A: strip EXIF + downscale + re-encode + compute pHash.
    # All downstream hashing, R2 upload, and dedup operate on the
    # sanitized bytes so the merchant view, the SHA-256 dedup key,
    # and the perceptual hash are all derived from the same image.
    try:
        sanitized = sanitize_proof_image(
            raw_bytes,
            content_type=file.content_type or "application/octet-stream",
        )
    except ProofImageDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Could not decode the uploaded image: {exc}",
        ) from exc

    image_bytes = sanitized.bytes
    image_content_type = sanitized.content_type
    perceptual_hash = sanitized.perceptual_hash

    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found.",
        )

    # Resolve per-store auto-approval thresholds from settings (the same
    # JSONB block the checkout branch read). Module defaults apply when
    # the merchant left a field unset.
    payment_settings = (
        (store.settings or {})
        .get("payment", {})
        .get(manual_settings_key(manual_method), {})
    )
    auto_config = AutoApprovalConfig(
        # Absent on stores configured before this existed, so it falls
        # back to the rail's default — off for Vodafone Cash, on for
        # InstaPay. A store only auto-approves wallet proofs after the
        # merchant deliberately switches it on.
        enabled=bool(
            payment_settings.get(
                "auto_approve_enabled",
                default_auto_approve_enabled(manual_method),
            )
        ),
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
        # Phase C OCR opt-in flags. Defaults match the module
        # defaults so a merchant who never touched these is
        # indistinguishable from one who explicitly disabled them.
        require_ocr_amount_match=bool(
            payment_settings.get("require_ocr_amount_match", False)
        ),
        require_ocr_ipa_match=bool(
            payment_settings.get("require_ocr_ipa_match", False)
        ),
        # Rail-specific default: Vodafone charges the sender a transfer
        # fee, so a 1% window would soft-block nearly every wallet
        # payment. See DEFAULT_VC_AMOUNT_TOLERANCE_BPS.
        ocr_amount_tolerance_bps=int(
            payment_settings.get("ocr_amount_tolerance_bps")
            or default_amount_tolerance_bps(manual_method)
        ),
        # Phase C extras
        require_note_contains_reference=bool(
            payment_settings.get("require_note_contains_reference", False)
        ),
        require_transaction_ref_match=bool(
            payment_settings.get("require_transaction_ref_match", False)
        ),
        require_recipient_name_match=bool(
            payment_settings.get("require_recipient_name_match", False)
        ),
    )
    merchant_recipient_name_token: str | None = (
        payment_settings.get("recipient_name_token") or None
    )
    # Phase A — per-store knob, default 5 of 64 bits ≈ 92% similarity.
    perceptual_dedup_max_distance = int(
        payment_settings.get("perceptual_dedup_max_distance", 5)
    )

    # Phase C — pick the OCR provider the admin assigned to this
    # store (or a Noop). The factory inspects ``store.settings`` so
    # we don't pre-resolve any sensitive keys here.
    vision_service = get_proof_vision_service_for_store(store.settings or {})

    # Decrypt the merchant's destination on this rail (IPA for
    # InstaPay, wallet number for Vodafone Cash) so the OCR
    # recipient-match rule can compare against it. Soft-fail: if
    # decryption blows up the rule simply no-ops (it's also gated on
    # the merchant's opt-in flag, so no observable behaviour change
    # for stores that haven't enabled it).
    merchant_ipa: str | None = None
    try:
        creds = await get_merchant_manual_credentials(
            store.settings or {}, manual_method
        )
        merchant_ipa = creds.get("destination")
    except Exception:
        merchant_ipa = None

    intent_repo = ManualPaymentIntentRepository(db)
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
        image_content_type=image_content_type,
        transaction_ref=transaction_ref,
        declared_amount_cents=declared_amount_cents,
        idempotency_key=idempotency_key,
        auto_approval_config=auto_config,
        image_perceptual_hash=perceptual_hash,
        perceptual_dedup_max_distance=perceptual_dedup_max_distance,
        vision_service=vision_service,
        merchant_ipa=merchant_ipa,
        merchant_recipient_name_token=merchant_recipient_name_token,
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


class CustomerProofRequirements(BaseModel):
    """What the customer must do / show on the screenshot, derived from
    the merchant's auto-approval flags. The storefront uses this to
    only ask for things the merchant actually enforces — silent flags
    don't pester the customer. Each field maps 1-1 to a server-side
    rule but uses customer-facing names so the storefront doesn't
    couple to internal rule tags."""

    # ``require_note_contains_reference`` → tell the customer to paste
    # the reference code into the bank app's Note / Reason field.
    note_must_contain_reference: bool = False
    # ``require_ocr_amount_match`` → amount must be visible in the
    # uploaded screenshot.
    screenshot_must_show_amount: bool = False
    # ``require_ocr_ipa_match`` → the recipient identifier must be
    # visible: the IPA on InstaPay, the wallet number on Vodafone Cash.
    screenshot_must_show_recipient_ipa: bool = False
    # ``require_recipient_name_match`` → recipient name must be visible.
    screenshot_must_show_recipient_name: bool = False
    # ``require_transaction_ref_match`` → bank's transaction reference
    # must be visible (so the customer-typed value can be cross-checked).
    screenshot_must_show_transaction_ref: bool = False


class InstapayStatusResponse(BaseModel):
    """Live state of a manual-rail payment for one order.

    Name is historical (InstaPay was the first rail). ``method`` and
    ``destination_kind`` tell the storefront which rail it is looking
    at; ``ipa`` stays populated for InstaPay so existing clients keep
    working, and is null on Vodafone Cash.
    """

    order_id: UUID
    # Shown on the resume page — a customer recognises ORD-482913, not
    # the order UUID that is in their address bar.
    order_number: str = ""
    reference_code: str
    # "instapay" | "vodafone_cash"
    method: str = "instapay"
    # The string the customer sends money to, rail-neutral.
    destination: str = ""
    # "ipa" | "wallet_number" — drives the field label the storefront
    # renders above ``destination``.
    destination_kind: str = "ipa"
    # False on Vodafone Cash: transfers start at *9# or in the Ana
    # Vodafone app, so the page must not render a QR block at all.
    supports_qr: bool = True
    ipa: str | None = None
    ipa_display_name: str | None = None
    fallback_phone: str | None = None
    amount_cents: int
    currency: str
    expires_at: datetime
    expires_in_seconds: int
    intent_status: str
    payment_status: str
    latest_proof: dict | None = None
    # Phase E — merchant-driven customer-facing requirements. Always
    # present (defaults all-false), so the storefront can render
    # without conditional null-checks.
    customer_requirements: CustomerProofRequirements = CustomerProofRequirements()
    # The scannable payload persisted on the intent. Returned so the
    # resume page renders the SAME QR the post-checkout page does —
    # without it, a customer who saw a QR at checkout and came back via
    # their email would find it missing. Null on rails with no QR.
    qr_payload: str | None = None
    # Public URL of the merchant's uploaded InstaPay QR image (taken
    # from inside their InstaPay app). The page falls back to showing
    # the IPA + reference when this is null.
    qr_image_url: str | None = None
    # InstaPay "Share link" URL — when set, the storefront renders a
    # QR code client-side from this string instead of showing the
    # uploaded image.
    qr_link_url: str | None = None
    # True when this InstaPay flow is paying a *deposit* on a COD
    # order rather than the full order amount. The storefront uses
    # this to swap the "Complete your payment" copy for a deposit
    # banner explaining the rest is collected on delivery.
    is_deposit: bool = False
    # Order total in cents (only set when ``is_deposit`` is true) so
    # the storefront can show "X EGP now, Y EGP on delivery". Null
    # for full-InstaPay flows where ``amount_cents`` already equals
    # the full order total.
    order_total_cents: int | None = None
    balance_due_cents: int | None = None


@router.get(
    "/orders/{order_id}/instapay-status",
    operation_id="storefront_get_instapay_status",
    response_model=SuccessResponse[InstapayStatusResponse],
    summary="Get current manual-payment intent + latest proof status",
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
                "Intent reference_code (returned in checkout payment_data) — "
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

    intent_repo = ManualPaymentIntentRepository(db)
    proof_repo = PaymentProofRepository(db)

    intent = await intent_repo.get_by_order_id(order_id)
    if intent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No payment intent exists for this order.",
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
    customer_requirements = CustomerProofRequirements()
    try:
        store = await store_repo.get_by_id(store_id)
        if store is not None:
            instapay_settings = (
                (store.settings or {})
                .get("payment", {})
                .get(manual_settings_key(intent.method), {})
            )
            qr_image_url = instapay_settings.get("qr_image_url")
            qr_link_url = instapay_settings.get("qr_link_url")
            ipa_display_name = instapay_settings.get("ipa_display_name")
            # Phase E — collapse the 5 merchant rule flags into the
            # customer-facing requirements payload. Defaults to all-
            # false, which means the storefront shows minimal copy.
            customer_requirements = CustomerProofRequirements(
                note_must_contain_reference=bool(
                    instapay_settings.get("require_note_contains_reference")
                ),
                screenshot_must_show_amount=bool(
                    instapay_settings.get("require_ocr_amount_match")
                ),
                screenshot_must_show_recipient_ipa=bool(
                    instapay_settings.get("require_ocr_ipa_match")
                ),
                screenshot_must_show_recipient_name=bool(
                    instapay_settings.get("require_recipient_name_match")
                ),
                screenshot_must_show_transaction_ref=bool(
                    instapay_settings.get("require_transaction_ref_match")
                ),
            )
    except Exception:
        # Non-fatal: the page still works without the QR/display name
        # and falls back to the all-false requirements default.
        pass

    # Deposit detection: a COD order with InstaPay as the deposit
    # gateway is paying a deposit, not the full order. The intent's
    # ``amount_cents`` already holds the deposit amount; we just need
    # to surface the order total + balance so the storefront can show
    # both numbers without extra round-trips.
    is_deposit = (
        order.payment_method == "cod" and order.deposit_gateway == intent.method.value
    )
    order_total_cents = order.total if is_deposit else None
    balance_due_cents = (
        max(0, order.total - intent.amount_cents) if is_deposit else None
    )

    return SuccessResponse(
        data=InstapayStatusResponse(
            order_id=order.id,
            order_number=order.order_number,
            reference_code=intent.reference_code,
            method=intent.method.value,
            destination=intent.display_destination,
            destination_kind=(
                "wallet_number"
                if intent.method is ManualPaymentMethod.VODAFONE_CASH
                else "ipa"
            ),
            supports_qr=intent.method is ManualPaymentMethod.INSTAPAY,
            qr_payload=(
                intent.qr_payload or None
                if intent.method is ManualPaymentMethod.INSTAPAY
                else None
            ),
            ipa=(
                intent.display_destination
                if intent.method is ManualPaymentMethod.INSTAPAY
                else None
            ),
            ipa_display_name=ipa_display_name,
            fallback_phone=intent.display_phone,
            amount_cents=intent.amount_cents,
            currency=order.currency,
            expires_at=intent.expires_at,
            expires_in_seconds=expires_in,
            intent_status=intent.status.value,
            payment_status=order.payment_status.value,
            latest_proof=latest_dict,
            qr_image_url=(
                qr_image_url if intent.method is ManualPaymentMethod.INSTAPAY else None
            ),
            qr_link_url=(
                qr_link_url if intent.method is ManualPaymentMethod.INSTAPAY else None
            ),
            is_deposit=is_deposit,
            order_total_cents=order_total_cents,
            balance_due_cents=balance_due_cents,
            customer_requirements=customer_requirements,
        )
    )
