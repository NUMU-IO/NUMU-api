"""Platform Kashier webhook — NUMU-directed card payments (wallet top-ups).

``POST /api/v1/webhooks/kashier/platform/callback``

Separate from the merchant Kashier webhook (order-first resolution with
per-merchant credentials): platform payments have no order and exactly
one secret, so the HMAC-SHA256 signature is verified against
``settings.platform_kashier_api_key`` and HARD-ENFORCED (401 on
mismatch). Intents resolve by ``merchantOrderId`` prefix ``WTOP-``.

Idempotency is belt-and-braces (same as the Paymob platform route):
Redis nonce on the transaction id, guarded intent-status transition,
and the ledger's unique ``idempotency_key`` (``kashier:{tx_id}``).
"""

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
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
from src.infrastructure.external_services.kashier import KashierPaymentService

logger = get_logger(__name__)
router = APIRouter()

_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)

NONCE_TTL_SECONDS = 86_400  # 24 hours
_TOPUP_PREFIX = "WTOP-"


@router.post("/platform/callback", operation_id="kashier_platform_callback")
async def kashier_platform_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    x_kashier_signature: str = Header(None, alias="x-kashier-signature"),
):
    payload = await request.body()
    log = logger.bind(webhook="kashier_platform")

    # ── Signature — hard-enforced with the platform API key ──────────
    if not settings.platform_kashier_api_key:
        log.error("platform_kashier_key_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform webhook not configured",
        )
    service = KashierPaymentService(
        mid=settings.platform_kashier_mid,
        api_key=settings.platform_kashier_api_key,
        mode=settings.platform_kashier_mode,
    )
    verified = service.verify_webhook_signature(payload, x_kashier_signature or "")
    if not verified:
        log.warning("platform_webhook_signature_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    raw = json.loads(payload)
    data = raw.get("data") or raw
    merchant_order_id = str(data.get("merchantOrderId") or "")
    transaction_id = data.get("transactionId")
    payment_status = (data.get("status") or data.get("paymentStatus") or "").upper()
    amount_str = str(data.get("amount") or "0")

    log = log.bind(
        transaction_id=transaction_id,
        merchant_order_id=merchant_order_id,
        payment_status=payment_status,
        amount=amount_str,
    )
    log.info("platform_webhook_received")

    # ── Replay protection ────────────────────────────────────────────
    if transaction_id and _cache_service:
        was_set = await _cache_service.set_if_absent(
            f"kashier:platform:processed:{transaction_id}",
            "1",
            expire=NONCE_TTL_SECONDS,
        )
        if not was_set:
            log.warning("platform_webhook_duplicate_rejected")
            return {"status": "duplicate", "transaction_id": transaction_id}

    # ── Resolve the top-up intent ────────────────────────────────────
    if not merchant_order_id.startswith(_TOPUP_PREFIX):
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

    if payment_status not in ("SUCCESS", "PAID", "CAPTURED"):
        if intent.status == TopupIntentStatus.PENDING.value:
            intent.status = TopupIntentStatus.FAILED.value
            intent.gateway_transaction_id = (
                str(transaction_id) if transaction_id else None
            )
            intent.failure_reason = payment_status or "declined"
            await db.commit()
        log.info("platform_topup_declined")
        return {"status": "received", "transaction_id": transaction_id}

    # ── Success → guarded transition + credit ────────────────────────
    # Accept pending AND expired/failed (late webhook after the sweep, or
    # a retried charge on the same session). Only succeeded is terminal —
    # and the ledger idempotency key backstops even that.
    if intent.status not in (
        TopupIntentStatus.PENDING.value,
        TopupIntentStatus.EXPIRED.value,
        TopupIntentStatus.FAILED.value,
    ):
        log.info("platform_topup_intent_not_creditable", status=intent.status)
        return {"status": "received", "transaction_id": transaction_id}

    # Kashier amounts are pound strings ("250.00") — compare in cents.
    try:
        paid_cents = int(round(float(amount_str) * 100))
    except (TypeError, ValueError):
        paid_cents = -1
    if paid_cents != intent.amount_cents:
        log.error(
            "platform_topup_amount_mismatch",
            intent_amount_cents=intent.amount_cents,
            paid_cents=paid_cents,
        )
        return {"status": "received", "transaction_id": transaction_id}

    intent.gateway_transaction_id = str(transaction_id)
    tx = await credit_topup_intent(
        db,
        intent=intent,
        idempotency_key=f"kashier:{transaction_id}",
        source="kashier_platform_webhook",
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
