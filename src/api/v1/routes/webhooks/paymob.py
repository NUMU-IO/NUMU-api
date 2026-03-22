"""Paymob webhook handler.

Receives payment notifications from Paymob for:
- Card payments (processed/declined)
- Wallet payments (processed/declined)
- Refunds
"""

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.external_services.paymob.payment_service import (
    PaymobPaymentService,
    get_merchant_paymob_credentials,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.tenancy.rls import narrow_to_tenant

logger = get_logger(__name__)
router = APIRouter()

_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)

NONCE_TTL_SECONDS = 86_400  # 24 hours


@router.post("/callback", operation_id="paymob_callback")
async def paymob_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    hmac: str = Header(None, alias="hmac"),
):
    """Handle Paymob payment callback.

    Paymob sends a POST request with payment transaction details.
    The HMAC header contains the signature for verification.

    Events handled:
    - Transaction approved (success=true)
    - Transaction declined (success=false)
    - Refund processed (is_refunded=true)
    """
    payload = await request.body()
    log = logger.bind(webhook="paymob")

    # Parse payload first to resolve the merchant
    data = json.loads(payload)

    # Extract transaction details
    obj = data.get("obj", {})
    transaction_id = obj.get("id")
    order_id = obj.get("order", {}).get("id")
    merchant_order_id = obj.get("order", {}).get("merchant_order_id")
    success = obj.get("success", False)
    is_refunded = obj.get("is_refunded", False)
    is_voided = obj.get("is_voided", False)
    amount_cents = obj.get("amount_cents", 0)
    currency = obj.get("currency", "EGP")

    log = log.bind(
        transaction_id=transaction_id,
        paymob_order_id=order_id,
        merchant_order_id=merchant_order_id,
        success=success,
        is_refunded=is_refunded,
        is_voided=is_voided,
        amount_cents=amount_cents,
        currency=currency,
    )
    log.info("webhook_received")

    # ── Replay protection ────────────────────────────────────────────
    if transaction_id and _cache_service:
        nonce_key = f"paymob:processed:{transaction_id}"
        was_set = await _cache_service.set_if_absent(
            nonce_key, "1", expire=NONCE_TTL_SECONDS
        )
        if not was_set:
            log.warning("webhook_duplicate_rejected")
            return {"status": "duplicate", "transaction_id": transaction_id}

    order_repo = OrderRepository(db)
    store_repo = StoreRepository(db)

    # Resolve the internal order via merchant_order_id (our payment_id)
    # or fall back to the Paymob order_id.
    order = None
    lookup_id = merchant_order_id or str(order_id) if order_id else None
    if lookup_id:
        order = await order_repo.get_by_payment_id_for_update(lookup_id)

    if not order:
        log.warning("webhook_order_not_found", lookup_id=lookup_id)
        return {"status": "received", "transaction_id": transaction_id}

    log = log.bind(order_id=str(order.id), order_number=order.order_number)

    # ── Per-merchant HMAC verification ────────────────────────────────
    store = await store_repo.get_by_id(order.store_id)
    if store:
        try:
            creds = await get_merchant_paymob_credentials(store.settings)
            service = PaymobPaymentService(hmac_secret=creds["hmac_secret"])
            verified_data = service.verify_webhook_signature(payload, hmac or "")
            if not verified_data:
                log.warning("webhook_signature_invalid")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid webhook signature",
                )
        except HTTPException:
            raise
        except Exception as e:
            # Credentials not configured or decryption failed — skip verification in dev
            log.warning("webhook_hmac_resolution_failed", error=str(e))
            if not settings.debug:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not verify webhook signature",
                )
    else:
        log.warning("webhook_store_not_found", store_id=str(order.store_id))

    # Narrow RLS from bypass → tenant-scoped for all subsequent writes
    await narrow_to_tenant(db, order.tenant_id)

    # Process based on event type
    if is_refunded:
        log.info("payment_refund_processed")
        order.refund(reason=f"Paymob refund - transaction {transaction_id}")
        await order_repo.update(order)

    elif is_voided:
        log.info("payment_voided")
        if order.can_be_cancelled:
            order.cancel(reason=f"Paymob void - transaction {transaction_id}")
            await order_repo.update(order)
        else:
            log.warning("payment_void_cannot_cancel", current_status=order.status.value)

    elif success:
        log.info("payment_success")
        order.mark_as_paid(
            payment_id=str(transaction_id),
            payment_method="paymob",
        )
        await order_repo.update(order)

    else:
        error_msg = obj.get("data", {}).get("message", "Payment failed")
        log.warning("payment_failed", error_message=error_msg)
        order.mark_payment_failed(reason=error_msg)
        await order_repo.update(order)

    # Always return 200 to acknowledge receipt
    return {"status": "received", "transaction_id": transaction_id}


@router.get("/callback", operation_id="paymob_callback_redirect")
async def paymob_callback_redirect(
    success: bool = False,
    txn_response_code: str | None = None,
    order: str | None = None,
    merchant_order_id: str | None = None,
):
    """Handle Paymob redirect after payment.

    After customer completes payment in iframe, they are redirected here.
    This is for redirect handling, not webhook processing.

    Query params from Paymob:
    - success: Payment success status
    - txn_response_code: Transaction response code
    - order: Paymob order ID
    - merchant_order_id: Your order ID
    """
    log = logger.bind(
        webhook="paymob_redirect",
        success=success,
        paymob_order_id=order,
        merchant_order_id=merchant_order_id,
        response_code=txn_response_code,
    )
    log.info("payment_redirect_received")

    # In production, redirect to frontend with status
    # For now, return status info
    return {
        "success": success,
        "order_id": merchant_order_id or order,
        "message": "Payment completed" if success else "Payment failed",
    }
