"""Fawaterak webhook handler.

Receives payment notifications from Fawaterak for:
- Paid transactions (invoice_status = "paid")
- Cancelled/expired transactions (status = "EXPIRED")
- Failed payments (errorMessage present)
- Refunds (status = "approved")
"""

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
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


async def _resolve_order(
    order_repo, invoice_id: str | None, payload_order_id: str | None
):
    """Try to find our internal order from Fawaterak callback data.

    Fawaterak sends our order_id back via the payLoad field.
    We also store the Fawaterak invoice_id as payment_id.
    """
    order = None

    # Try payLoad (our order UUID) first
    if payload_order_id:
        try:
            uuid_val = UUID(payload_order_id)
            order = await order_repo.get_by_id(uuid_val)
        except (ValueError, AttributeError):
            pass

        if not order:
            order = await order_repo.get_by_payment_id_for_update(payload_order_id)

    # Fall back to Fawaterak invoice_id
    if not order and invoice_id:
        order = await order_repo.get_by_payment_id_for_update(str(invoice_id))

    return order


@router.post("/callback", operation_id="fawaterak_callback")
async def fawaterak_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
):
    """Handle Fawaterak payment webhook (POST).

    Fawaterak sends different payloads for:
    - Paid: {hashKey, invoice_key, invoice_id, payment_method, invoice_status, pay_load, referenceNumber}
    - Cancelled: {hashKey, referenceId, status, paymentMethod, pay_load, transactionId, transactionKey}
    - Failed: {invoice_key, invoice_id, payment_method, pay_load, amount, errorMessage, referenceNumber}
    - Refund: {transactionId, amount, currency, status, reason, approvedAt}
    """
    payload = await request.body()
    log = logger.bind(webhook="fawaterak")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        log.warning("webhook_invalid_json")
        return {"status": "error", "message": "Invalid JSON"}

    # Determine event type
    invoice_status = data.get("invoice_status")
    cancelled_status = data.get("status")
    error_message = data.get("errorMessage")
    is_refund = cancelled_status == "approved" and "reason" in data

    # Extract identifiers
    invoice_id = data.get("invoice_id")
    transaction_id = data.get("transactionId")
    reference_number = data.get("referenceNumber") or data.get("referenceId")
    payment_method = data.get("payment_method") or data.get("paymentMethod", "")
    pay_load = data.get("pay_load")  # Our order_id
    amount = data.get("amount", 0)

    # pay_load may be a JSON string or direct value
    payload_order_id = None
    if pay_load:
        if isinstance(pay_load, str):
            try:
                parsed = json.loads(pay_load)
                payload_order_id = str(parsed) if parsed else None
            except (json.JSONDecodeError, TypeError):
                payload_order_id = pay_load
        else:
            payload_order_id = str(pay_load)

    log = log.bind(
        invoice_id=invoice_id,
        transaction_id=transaction_id,
        reference_number=reference_number,
        payment_method=payment_method,
        invoice_status=invoice_status,
        payload_order_id=payload_order_id,
    )
    log.info("webhook_received")

    # Replay protection
    nonce_id = transaction_id or invoice_id or reference_number
    if nonce_id and _cache_service:
        nonce_key = f"fawaterak:processed:{nonce_id}"
        was_set = await _cache_service.set_if_absent(
            nonce_key, "1", expire=NONCE_TTL_SECONDS
        )
        if not was_set:
            log.warning("webhook_duplicate_rejected")
            return {"status": "duplicate", "id": nonce_id}

    order_repo = OrderRepository(db)
    store_repo = StoreRepository(db)

    order = await _resolve_order(
        order_repo, str(invoice_id) if invoice_id else None, payload_order_id
    )

    if not order:
        log.warning("webhook_order_not_found")
        return {"status": "received", "message": "Order not found"}

    log = log.bind(order_id=str(order.id), order_number=order.order_number)

    # Per-merchant HMAC verification
    store = await store_repo.get_by_id(order.store_id)
    if store:
        try:
            from src.infrastructure.external_services.fawaterak.payment_service import (
                FawaterakPaymentService,
                get_merchant_fawaterak_credentials,
            )

            creds = await get_merchant_fawaterak_credentials(store.settings)
            service = FawaterakPaymentService(vendor_key=creds.get("vendor_key"))
            verified_data = service.verify_webhook_signature(payload, "")
            if not verified_data:
                log.warning("webhook_signature_invalid_proceeding")
        except Exception as e:
            log.warning("webhook_hmac_resolution_failed", error=str(e))

    # Narrow RLS for subsequent writes
    await narrow_to_tenant(db, order.tenant_id)

    if is_refund:
        # Refund webhook
        log.info("payment_refund_processed")
        reason = data.get("reason", "Fawaterak refund")
        order.refund(reason=f"Fawaterak refund - {reason}")
        await order_repo.update(order)

    elif cancelled_status == "EXPIRED":
        # Cancelled/expired transaction
        log.info("payment_expired")
        if order.can_be_cancelled:
            order.cancel(reason=f"Fawaterak payment expired - ref {reference_number}")
            await order_repo.update(order)

    elif error_message:
        # Failed payment
        log.warning("payment_failed", error_message=error_message)
        order.mark_payment_failed(reason=error_message)
        await order_repo.update(order)

        # Record failed transaction
        tx = PaymentTransactionModel(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            channel="online",
            gateway="fawaterak",
            amount_cents=int(float(amount) * 100) if amount else 0,
            currency=data.get("paidCurrency", "EGP"),
            status="failed",
            failure_reason=error_message,
            gateway_transaction_id=str(reference_number or invoice_id or ""),
            processing_completed_at=datetime.now(UTC),
        )
        db.add(tx)
        await db.flush()

    elif invoice_status == "paid":
        # Successful payment
        log.info("payment_success")
        old_status = (
            order.status.value if hasattr(order.status, "value") else str(order.status)
        )
        order.mark_as_paid(
            payment_id=str(invoice_id or reference_number),
            payment_method="fawaterak",
        )
        await order_repo.update(order)

        # Update real-time revenue counter
        try:
            from src.infrastructure.cache.realtime_counters import record_payment

            await record_payment(order.store_id, order.total)
        except Exception:
            pass

        # Emit funnel event: order_completed
        try:
            from src.infrastructure.repositories.funnel_event_repository import (
                FunnelEventRepository,
            )

            fe_repo = FunnelEventRepository(db)
            await fe_repo.create(
                tenant_id=order.tenant_id,
                store_id=order.store_id,
                step="order_completed",
                customer_id=order.customer_id,
                session_fingerprint=order.session_fingerprint,
                step_data={
                    "order_id": str(order.id),
                    "total": order.total,
                    "payment_method": f"fawaterak_{payment_method}",
                },
            )
        except Exception:
            pass

        # Meta CAPI Purchase fan-out — server-side authoritative for
        # Purchase per plan §5.4. Best-effort.
        try:
            from src.application.services.meta_capi_purchase_dispatcher import (
                enqueue_meta_capi_purchase,
            )

            await enqueue_meta_capi_purchase(db, order)
        except Exception:
            log.warning("meta_capi_purchase_enqueue_failed", exc_info=True)

        # Fire OrderStatusChangedEvent
        try:
            from src.core.events.order_events import OrderStatusChangedEvent
            from src.infrastructure.events.setup import get_event_bus

            sr = StoreRepository(db)
            store = await sr.get_by_id(order.store_id)
            event = OrderStatusChangedEvent(
                order_id=order.id,
                order_number=order.order_number,
                store_id=order.store_id,
                store_name=store.name if store else "",
                customer_id=order.customer_id,
                customer_name=(
                    order.shipping_address.full_name if order.shipping_address else None
                ),
                previous_status=old_status,
                new_status="processing",
            )
            get_event_bus().publish(event)
            log.info("order_status_event_dispatched", new_status="processing")
        except Exception as e:
            log.warning("order_status_event_failed", error=str(e))

        # Create payment transaction record
        amount_cents = int(float(amount) * 100) if amount else order.total
        tx = PaymentTransactionModel(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            channel="online",
            gateway="fawaterak",
            display_name=f"Fawaterak {payment_method}",
            amount_cents=amount_cents,
            currency=data.get("paidCurrency", order.currency or "EGP"),
            status="success",
            gateway_transaction_id=str(reference_number or invoice_id or ""),
            processing_completed_at=datetime.now(UTC),
        )
        db.add(tx)
        await db.flush()
        log.info("payment_transaction_created", tx_id=str(tx.id))

        # Generate invoice
        from src.api.v1.routes.webhooks._invoice_helper import (
            generate_invoice_for_paid_order,
        )

        await generate_invoice_for_paid_order(
            db=db,
            order_id=order.id,
            store_id=order.store_id,
            tenant_id=order.tenant_id,
            customer_email=None,
        )

    else:
        log.warning("webhook_unrecognized_event", data=data)

    return {"status": "received"}


@router.post("/callback_json", operation_id="fawaterak_callback_json")
async def fawaterak_callback_json(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
):
    """Handle Fawaterak JSON webhook (POST).

    Fawaterak docs say append _json to the webhook URL for JSON format.
    This endpoint is an alias for the main callback.
    """
    return await fawaterak_callback(request, db)
