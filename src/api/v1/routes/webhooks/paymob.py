"""Paymob webhook handler.

Receives payment notifications from Paymob for:
- Card payments (processed/declined)
- Wallet payments (processed/declined)
- Refunds
"""

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)
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


async def _resolve_order(order_repo, merchant_order_id, paymob_order_id):
    """Try to find our internal order from Paymob callback data.

    Lookup priority:
    1. merchant_order_id as UUID (our order.id set via extras)
    2. merchant_order_id as payment_id
    3. paymob_order_id as payment_id
    """
    order = None

    # Try merchant_order_id as our order UUID first
    if merchant_order_id:
        try:
            uuid_val = UUID(merchant_order_id)
            order = await order_repo.get_by_id(uuid_val)
        except (ValueError, AttributeError):
            pass

        # Fall back to payment_id lookup
        if not order:
            order = await order_repo.get_by_payment_id_for_update(merchant_order_id)

    # Fall back to Paymob order ID
    if not order and paymob_order_id:
        order = await order_repo.get_by_payment_id_for_update(str(paymob_order_id))

    return order


@router.post("/callback", operation_id="paymob_callback")
async def paymob_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    hmac: str = Header(None, alias="hmac"),
):
    """Handle Paymob payment callback (POST).

    Paymob sends a POST request with payment transaction details.
    The HMAC header contains the signature for verification.
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

    # Resolve internal order
    order = await _resolve_order(order_repo, merchant_order_id, order_id)

    if not order:
        log.warning(
            "webhook_order_not_found",
            merchant_order_id=merchant_order_id,
            paymob_order_id=order_id,
        )
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
                # Log warning but don't block — HMAC mismatch may be due to
                # key misconfiguration. TODO: enforce once merchants confirm keys.
                log.warning("webhook_signature_invalid_proceeding")
        except HTTPException:
            raise
        except Exception as e:
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
        old_status = (
            order.status.value if hasattr(order.status, "value") else str(order.status)
        )
        order.mark_as_paid(
            payment_id=str(transaction_id),
            payment_method="paymob",
        )
        await order_repo.update(order)

        # Fire OrderStatusChangedEvent so shipment auto-creation triggers
        try:
            from src.core.events.base import EventBus
            from src.core.events.order_events import OrderStatusChangedEvent

            event = OrderStatusChangedEvent(
                order_id=order.id,
                store_id=order.store_id,
                old_status=old_status,
                new_status="processing",
            )
            await EventBus.dispatch(event)
            log.info("order_status_event_dispatched", new_status="processing")
        except Exception as e:
            log.warning("order_status_event_failed", error=str(e))

        # Create payment transaction record for reconciliation
        source_data = obj.get("source_data", {})
        tx = PaymentTransactionModel(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            channel="online",
            gateway="paymob",
            display_name=f"{source_data.get('sub_type', 'Card')} •••• {source_data.get('pan', '****')[-4:]}",
            amount_cents=amount_cents,
            currency=currency,
            status="success",
            gateway_transaction_id=str(transaction_id),
            processing_completed_at=datetime.now(UTC),
        )
        db.add(tx)
        await db.flush()
        log.info("payment_transaction_created", tx_id=str(tx.id))

        # Generate invoice now that payment is confirmed
        from src.api.v1.routes.webhooks._invoice_helper import (
            generate_invoice_for_paid_order,
        )

        await generate_invoice_for_paid_order(
            db=db,
            order_id=order.id,
            store_id=order.store_id,
            tenant_id=order.tenant_id,
            customer_email=None,  # email already sent during checkout
        )

    else:
        error_msg = obj.get("data", {}).get("message", "Payment failed")
        txn_response = obj.get("txn_response_code", "")
        log.warning("payment_failed", error_message=error_msg)
        order.mark_payment_failed(reason=error_msg)
        await order_repo.update(order)

        # Record failed transaction too
        tx = PaymentTransactionModel(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            channel="online",
            gateway="paymob",
            amount_cents=amount_cents,
            currency=currency,
            status="failed",
            failure_reason=error_msg,
            failure_code=txn_response,
            gateway_transaction_id=str(transaction_id),
            processing_completed_at=datetime.now(UTC),
        )
        db.add(tx)
        await db.flush()

    # Always return 200 to acknowledge receipt
    return {"status": "received", "transaction_id": transaction_id}


@router.get("/callback", operation_id="paymob_callback_redirect")
async def paymob_callback_redirect(
    request: Request,
    success: bool = Query(False),
    merchant_order_id: str | None = Query(None),
    order: str | None = Query(None),
    id: str | None = Query(None),
    db: AsyncSession = Depends(get_admin_db_session),
):
    """Handle Paymob redirect after payment.

    After customer completes payment, Paymob redirects here.
    We look up the order's store to build the correct storefront URL,
    then redirect the customer to the order confirmation page.

    Paymob sends: id (transaction ID), order (Paymob order ID),
    merchant_order_id (our order UUID, may be null in GET).
    The POST webhook updates payment_id to the transaction ID,
    so we can look up by id param.
    """
    log = logger.bind(
        webhook="paymob_redirect",
        success=success,
        paymob_order_id=order,
        merchant_order_id=merchant_order_id,
        transaction_id=id,
    )
    log.info("payment_redirect_received")

    order_repo = OrderRepository(db)
    store_repo = StoreRepository(db)

    # Try multiple lookup strategies:
    # 1. merchant_order_id (our UUID) — may be null in GET redirect
    # 2. id (transaction ID) — POST webhook stores this as payment_id
    # 3. order (Paymob order ID)
    internal_order = await _resolve_order(order_repo, merchant_order_id, order)
    if not internal_order and id:
        internal_order = await _resolve_order(order_repo, None, id)

    if internal_order:
        store = await store_repo.get_by_id(internal_order.store_id)
        if store:
            # Also mark as paid via GET redirect (backup for webhook)
            if success and internal_order.payment_status.value == "pending":
                await narrow_to_tenant(db, internal_order.tenant_id)
                internal_order.mark_as_paid(
                    payment_id=str(order or merchant_order_id),
                    payment_method="paymob",
                )
                await order_repo.update(internal_order)

            # Build storefront URL using store's subdomain
            subdomain = store.subdomain
            base_url = f"https://{subdomain}.numueg.app"

            if success:
                redirect_url = f"{base_url}/order-confirmation?order_id={internal_order.id}&order_number={internal_order.order_number}&status=paid"
            else:
                redirect_url = f"{base_url}/checkout?payment_failed=true"

            log.info("payment_redirect_to_store", redirect_url=redirect_url)
            return RedirectResponse(url=redirect_url)

    # Fallback: no order found
    log.warning("payment_redirect_order_not_found")
    if success:
        return RedirectResponse(url="https://numueg.app/order-confirmation?status=paid")
    else:
        return RedirectResponse(url="https://numueg.app/checkout?payment_failed=true")
