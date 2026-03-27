"""Kashier webhook handler.

Receives payment notifications from Kashier for:
- Card payments (success/failure)
- Wallet payments (success/failure)

Supports both legacy flat payloads and session-based nested payloads.
"""

import base64
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
from src.infrastructure.external_services.kashier import KashierPaymentService
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.tenancy.rls import narrow_to_tenant

logger = get_logger(__name__)
router = APIRouter()

_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)

NONCE_TTL_SECONDS = 86_400  # 24 hours


def _extract_webhook_fields(raw: dict) -> dict:
    """Extract payment fields from Kashier webhook payload.

    Kashier webhook format (from docs):
    {
      "event": "pay",
      "data": {
        "merchantOrderId": "...",
        "kashierOrderId": "...",
        "transactionId": "...",
        "status": "SUCCESS",
        "amount": "69.00",
        "currency": "EGP",
        "maskedCard": "****1234",
        "cardBrand": "Visa",
        ...
      },
      "signatureKeys": [...]
    }
    """
    # Data is nested under "data" key
    data = raw.get("data") or raw

    return {
        "event": raw.get("event"),
        "merchant_order_id": data.get("merchantOrderId"),
        "transaction_id": data.get("transactionId"),
        "payment_status": data.get("status") or data.get("paymentStatus"),
        "kashier_order_id": data.get("kashierOrderId") or data.get("orderId"),
        "amount": data.get("amount"),
        "currency": data.get("currency") or "EGP",
        "card_brand": data.get("cardBrand") or "",
        "masked_card": data.get("maskedCard") or "",
        "card_token": data.get("cardDataToken") or "",
        "signature_keys": raw.get("signatureKeys") or [],
    }


async def _get_kashier_api_key(store_settings: dict) -> str | None:
    """Get Kashier API key from store.settings encrypted credentials."""
    kashier_settings = (store_settings or {}).get("payment", {}).get("kashier", {})
    if not kashier_settings.get("encrypted_credentials"):
        return None

    try:
        from src.infrastructure.external_services.secrets.secrets_manager import (
            get_secrets_manager,
        )

        secrets = get_secrets_manager()
        key_id = kashier_settings["encryption_key_id"]
        encrypted = base64.b64decode(kashier_settings["encrypted_credentials"])
        creds = await secrets.decrypt(encrypted, key_id)
        return creds.get("api_key")
    except Exception as e:
        logger.error(f"Failed to decrypt Kashier credentials: {e}")
        return None


@router.post("/callback", operation_id="kashier_callback")
async def kashier_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    x_kashier_signature: str = Header(None, alias="x-kashier-signature"),
):
    """Handle Kashier payment callback."""
    payload = await request.body()
    log = logger.bind(webhook="kashier")

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        log.warning("webhook_invalid_json")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    # Log raw payload for debugging
    log.info("webhook_raw_payload", payload_keys=list(data.keys()))

    fields = _extract_webhook_fields(data)
    merchant_order_id = fields["merchant_order_id"]
    transaction_id = fields["transaction_id"]
    payment_status = fields["payment_status"]
    amount = fields["amount"]
    currency = fields["currency"]

    log = log.bind(
        transaction_id=transaction_id,
        kashier_order_id=fields["kashier_order_id"],
        merchant_order_id=merchant_order_id,
        payment_status=payment_status,
        amount=amount,
        currency=currency,
    )
    log.info("webhook_received")

    # ── Replay protection ────────────────────────────────────────
    if transaction_id and _cache_service:
        nonce_key = f"kashier:processed:{transaction_id}"
        was_set = await _cache_service.set_if_absent(
            nonce_key, "1", expire=NONCE_TTL_SECONDS
        )
        if not was_set:
            log.warning("webhook_duplicate_rejected")
            return {"status": "duplicate", "transaction_id": transaction_id}

    # ── Order lookup ─────────────────────────────────────────────
    order_repo = OrderRepository(db)
    store_repo = StoreRepository(db)
    order = None

    # Try merchant_order_id as UUID (our order ID)
    if merchant_order_id:
        try:
            uuid_val = UUID(merchant_order_id)
            order = await order_repo.get_by_id(uuid_val)
        except (ValueError, AttributeError):
            pass

        if not order:
            order = await order_repo.get_by_payment_id_for_update(merchant_order_id)

    if not order:
        log.warning("webhook_order_not_found", lookup_id=merchant_order_id)
        return {"status": "received", "transaction_id": transaction_id}

    log = log.bind(order_id=str(order.id), order_number=order.order_number)

    # ── Signature verification via store.settings credentials ────
    store = await store_repo.get_by_id(order.store_id)
    if store:
        api_key = await _get_kashier_api_key(store.settings)
        if api_key:
            kashier_service = KashierPaymentService(api_key=api_key)
            verified = kashier_service.verify_webhook_signature(
                payload, x_kashier_signature or ""
            )
            if not verified:
                log.warning("webhook_signature_invalid_proceeding")
        else:
            log.warning("webhook_no_api_key")

    # ── RLS narrowing ────────────────────────────────────────────
    await narrow_to_tenant(db, order.tenant_id)

    # ── Process payment status ───────────────────────────────────
    status_upper = (payment_status or "").upper()

    if status_upper == "SUCCESS":
        log.info("payment_success")
        old_status = (
            order.status.value if hasattr(order.status, "value") else str(order.status)
        )
        order.mark_as_paid(
            payment_id=str(transaction_id or fields["kashier_order_id"] or ""),
            payment_method="kashier",
        )
        await order_repo.update(order)

        # Fire OrderStatusChangedEvent so shipment auto-creation triggers
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
                customer_name=order.shipping_address.full_name
                if order.shipping_address
                else None,
                previous_status=old_status,
                new_status="processing",
            )
            get_event_bus().publish(event)
            log.info("order_status_event_dispatched", new_status="processing")
        except Exception as e:
            log.warning("order_status_event_failed", error=str(e))

        # Create transaction record for reconciliation
        tx = PaymentTransactionModel(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            channel="online",
            gateway="kashier",
            display_name=f"{fields['card_brand']} •••• {fields['masked_card'][-4:]}"
            if fields["masked_card"]
            else "Card",
            amount_cents=int(float(amount or 0) * 100),
            currency=currency,
            status="success",
            gateway_transaction_id=str(transaction_id or ""),
            processing_completed_at=datetime.now(UTC),
        )
        db.add(tx)
        await db.flush()
        log.info("payment_transaction_created", tx_id=str(tx.id))

        # Save card token for one-click upsell charges
        card_token = fields.get("card_token")
        if card_token and order.customer_id:
            from src.infrastructure.database.models.tenant.saved_payment_method import (
                SavedPaymentMethodModel,
            )

            saved = SavedPaymentMethodModel(
                customer_id=order.customer_id,
                store_id=order.store_id,
                order_id=order.id,
                gateway="kashier",
                card_token=card_token,
                display_name=f"{fields['card_brand']} •••• {fields['masked_card'][-4:]}"
                if fields["masked_card"]
                else "Card",
                card_brand=fields["card_brand"] or None,
                last_four=fields["masked_card"][-4:] if fields["masked_card"] else None,
            )
            db.add(saved)
            await db.flush()
            log.info("card_token_saved", saved_id=str(saved.id))

        # Generate invoice now that payment is confirmed
        from src.api.v1.routes.webhooks._invoice_helper import (
            generate_invoice_for_paid_order,
        )

        await generate_invoice_for_paid_order(
            db=db,
            order_id=order.id,
            store_id=order.store_id,
            tenant_id=order.tenant_id,
        )

    elif status_upper == "FAILED":
        error_msg = data.get("error", {}).get("message", "Payment failed")
        log.warning("payment_failed", error_message=error_msg)
        order.mark_payment_failed(reason=error_msg)
        await order_repo.update(order)

    else:
        log.info("webhook_status_no_action", status=payment_status)

    return {"status": "received", "transaction_id": transaction_id}


@router.get("/redirect", operation_id="kashier_redirect")
async def kashier_redirect(
    order_id: str | None = Query(None),
    paymentStatus: str | None = Query(None),
    db: AsyncSession = Depends(get_admin_db_session),
):
    """Handle Kashier merchant redirect after payment."""
    log = logger.bind(
        webhook="kashier_redirect", order_id=order_id, payment_status=paymentStatus
    )
    log.info("redirect_received")

    order_repo = OrderRepository(db)
    store_repo = StoreRepository(db)

    internal_order = None
    if order_id:
        try:
            uuid_val = UUID(order_id)
            internal_order = await order_repo.get_by_id(uuid_val)
        except (ValueError, AttributeError):
            internal_order = await order_repo.get_by_payment_id_for_update(order_id)

    if internal_order:
        store = await store_repo.get_by_id(internal_order.store_id)
        if store:
            subdomain = store.subdomain
            base_url = f"https://{subdomain}.numueg.app"

            success = (paymentStatus or "").upper() == "SUCCESS"

            # Backup: mark as paid if webhook hasn't processed yet
            if success and internal_order.payment_status.value == "pending":
                await narrow_to_tenant(db, internal_order.tenant_id)
                internal_order.mark_as_paid(
                    payment_id=order_id or "",
                    payment_method="kashier",
                )
                await order_repo.update(internal_order)

            if success:
                redirect_url = f"{base_url}/order-confirmation?order_id={internal_order.id}&order_number={internal_order.order_number}&status=paid"
            else:
                redirect_url = f"{base_url}/checkout?payment_failed=true"

            return RedirectResponse(url=redirect_url)

    # Fallback
    return RedirectResponse(url="https://numueg.app")
