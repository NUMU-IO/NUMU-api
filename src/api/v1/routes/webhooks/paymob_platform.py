"""Platform Paymob webhook — NUMU-directed payments (wallet top-ups).

``POST /api/v1/webhooks/paymob/platform/callback``

Deliberately a separate route from the merchant webhook
(:mod:`src.api.v1.routes.webhooks.paymob`): that handler resolves an
ORDER first and verifies against the MERCHANT's HMAC secret with soft
enforcement. Platform payments have no order and exactly one secret, so:

* HMAC-SHA512 is verified against ``settings.platform_paymob_hmac_secret``
  and HARD-ENFORCED — 401 on mismatch, no soft mode, from day one.
* Intents are resolved by ``merchant_order_id`` prefix ``WTOP-``.
  Unknown references return 200 (a future platform SKU — e.g. invoice
  payments — can branch here).
* Idempotency is belt-and-braces: Redis nonce on the transaction id, a
  guarded intent-status transition, and the ledger's unique
  ``idempotency_key`` (``paymob:{transaction_id}``).

Also handles the GET redirect leg: Paymob sends the merchant's browser
back here when no ``redirection_url`` reached the intention; we bounce
to the hub Wallet page.
"""

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.wallet.credit_wallet import (
    credit_topup_intent,
    notify_topup_credited,
)
from src.config import settings
from src.core.entities.wallet import TopupIntentStatus
from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.database.models.public.wallet import (
    WalletTopupIntentModel,
)
from src.infrastructure.external_services.paymob.payment_service import (
    PaymobPaymentService,
)

logger = get_logger(__name__)
router = APIRouter()

_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)

NONCE_TTL_SECONDS = 86_400  # 24 hours
_TOPUP_PREFIX = "WTOP-"


@router.post("/platform/callback", operation_id="paymob_platform_callback")
async def paymob_platform_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    hmac: str = Header(None, alias="hmac"),
):
    payload = await request.body()
    log = logger.bind(webhook="paymob_platform")

    # ── HMAC — hard-enforced with the platform secret ────────────────
    if not settings.platform_paymob_hmac_secret:
        log.error("platform_hmac_secret_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform webhook not configured",
        )
    service = PaymobPaymentService(hmac_secret=settings.platform_paymob_hmac_secret)
    verified = service.verify_webhook_signature(payload, hmac or "")
    if not verified:
        log.warning("platform_webhook_signature_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    data = json.loads(payload)
    obj = data.get("obj", {})
    transaction_id = obj.get("id")
    merchant_order_id = obj.get("order", {}).get("merchant_order_id") or ""
    success = obj.get("success", False)
    is_refunded = obj.get("is_refunded", False)
    amount_cents = obj.get("amount_cents", 0)

    log = log.bind(
        transaction_id=transaction_id,
        merchant_order_id=merchant_order_id,
        success=success,
        is_refunded=is_refunded,
        amount_cents=amount_cents,
    )
    log.info("platform_webhook_received")

    # ── Replay protection ────────────────────────────────────────────
    if transaction_id and _cache_service:
        was_set = await _cache_service.set_if_absent(
            f"paymob:platform:processed:{transaction_id}",
            "1",
            expire=NONCE_TTL_SECONDS,
        )
        if not was_set:
            log.warning("platform_webhook_duplicate_rejected")
            return {"status": "duplicate", "transaction_id": transaction_id}

    # ── Resolve the top-up intent ────────────────────────────────────
    if not merchant_order_id.startswith(_TOPUP_PREFIX):
        # Not a wallet top-up. Future platform payments (e.g. one-off
        # invoice settlements) dispatch from here.
        log.info("platform_webhook_unknown_reference")
        return {"status": "received", "transaction_id": transaction_id}

    intent = (
        await db.execute(
            select(WalletTopupIntentModel)
            .where(WalletTopupIntentModel.special_reference == merchant_order_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if intent is None:
        log.warning("platform_webhook_intent_not_found")
        return {"status": "received", "transaction_id": transaction_id}

    log = log.bind(intent_id=str(intent.id), tenant_id=str(intent.tenant_id))

    if is_refunded:
        # A refunded top-up needs a human decision (claw back vs leave):
        # never auto-debit a wallet that may already have spent the funds.
        log.error("platform_topup_refunded_needs_review")
        return {"status": "received", "transaction_id": transaction_id}

    if not success:
        if intent.status == TopupIntentStatus.PENDING.value:
            intent.status = TopupIntentStatus.FAILED.value
            intent.gateway_transaction_id = str(transaction_id)
            intent.failure_reason = obj.get("data", {}).get("message") or "declined"
            await db.commit()
        log.info("platform_topup_declined")
        return {"status": "received", "transaction_id": transaction_id}

    # ── Success → guarded transition + credit ────────────────────────
    # Accept pending AND expired (a late webhook after the expiry sweep
    # must still credit real money) and failed (a retried charge on the
    # same intention after an initial decline). Only 'succeeded' is
    # terminal — and the ledger idempotency key backstops even that.
    if intent.status not in (
        TopupIntentStatus.PENDING.value,
        TopupIntentStatus.EXPIRED.value,
        TopupIntentStatus.FAILED.value,
    ):
        log.info("platform_topup_intent_not_creditable", status=intent.status)
        return {"status": "received", "transaction_id": transaction_id}

    if int(amount_cents or 0) != intent.amount_cents:
        # Paid amount differs from the intent — never credit blindly.
        log.error(
            "platform_topup_amount_mismatch",
            intent_amount_cents=intent.amount_cents,
        )
        return {"status": "received", "transaction_id": transaction_id}

    intent.gateway_transaction_id = str(transaction_id)
    tx = await credit_topup_intent(
        db,
        intent=intent,
        idempotency_key=f"paymob:{transaction_id}",
        source="paymob_platform_webhook",
    )
    await db.commit()

    if tx is not None:
        await notify_topup_credited(
            db,
            tenant_id=intent.tenant_id,
            amount_cents=intent.amount_cents,
            balance_after_cents=tx.balance_after_cents,
        )
        log.info("platform_topup_credited")
    return {"status": "processed", "transaction_id": transaction_id}


@router.get("/platform/callback", operation_id="paymob_platform_redirect")
async def paymob_platform_redirect(request: Request):
    """Browser return leg — bounce the merchant back to the hub wallet.

    State never changes here; the POST webhook is the source of truth.
    The hub polls the top-up status on arrival.
    """
    params = dict(request.query_params)
    merchant_order_id = params.get("merchant_order_id") or ""
    topup_id = (
        merchant_order_id[len(_TOPUP_PREFIX) :]
        if merchant_order_id.startswith(_TOPUP_PREFIX)
        else ""
    )
    suffix = f"?topup_id={topup_id}" if topup_id else ""
    return RedirectResponse(
        url=f"{settings.merchant_hub_url}/wallet{suffix}",
        status_code=302,
    )
