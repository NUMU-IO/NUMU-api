"""Moyasar webhook handler (Saudi Arabia).

Receives payment notifications from Moyasar for invoice/card/mada/Apple Pay
payments. Authenticates each delivery via the shared ``secret_token`` carried
in the payload (compared against the merchant's stored webhook secret), then
marks the matching order paid and generates its invoice.

Payload shape (Moyasar):
    {
      "type": "payment_paid",
      "secret_token": "...",
      "data": {
        "id": "<payment_id>",
        "status": "paid",
        "amount": 11500,
        "currency": "SAR",
        "source": {"type": "creditcard", "company": "visa", ...},
        "metadata": {"order_id": "<our order id>"},
        "invoice_id": "..."
      }
    }
"""

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.payment import get_tenant_payment_service
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

_PAID_TYPES = {"payment_paid"}
_FAILED_TYPES = {"payment_failed", "payment_voided"}


def _extract_fields(raw: dict) -> dict:
    """Flatten the Moyasar webhook payload to the fields we need."""
    data = raw.get("data") or {}
    source = data.get("source") or {}
    metadata = data.get("metadata") or {}
    return {
        "type": raw.get("type"),
        "payment_id": data.get("id"),
        "status": (data.get("status") or "").lower(),
        "amount": data.get("amount"),
        "currency": data.get("currency") or "SAR",
        "order_id": metadata.get("order_id"),
        "invoice_id": data.get("invoice_id"),
        "card_brand": source.get("company") or "",
        "card_last_four": (source.get("number") or "")[-4:],
        "failure_message": data.get("message") or source.get("message") or "",
    }


@router.post("/callback", operation_id="moyasar_callback")
async def moyasar_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
):
    """Handle a Moyasar payment webhook."""
    payload = await request.body()
    log = logger.bind(webhook="moyasar")

    try:
        raw = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        log.warning("webhook_invalid_json")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
        )

    fields = _extract_fields(raw)
    payment_id = fields["payment_id"]
    order_id = fields["order_id"]
    log = log.bind(
        event_type=fields["type"],
        payment_id=payment_id,
        order_id=order_id,
        status=fields["status"],
    )
    log.info("webhook_received")

    # ── Replay protection ────────────────────────────────────────────
    if payment_id and _cache_service:
        nonce_key = f"moyasar:processed:{payment_id}:{fields['type']}"
        was_set = await _cache_service.set_if_absent(
            nonce_key, "1", expire=NONCE_TTL_SECONDS
        )
        if not was_set:
            log.warning("webhook_duplicate_rejected")
            return {"status": "duplicate", "payment_id": payment_id}

    # ── Order lookup ─────────────────────────────────────────────────
    order_repo = OrderRepository(db)
    store_repo = StoreRepository(db)
    order = None
    if order_id:
        try:
            order = await order_repo.get_by_id(UUID(str(order_id)))
        except (ValueError, AttributeError):
            order = await order_repo.get_by_payment_id_for_update(str(order_id))

    if not order:
        log.warning("webhook_order_not_found", lookup_id=order_id)
        return {"status": "received", "payment_id": payment_id}

    log = log.bind(order_id=str(order.id), order_number=order.order_number)

    # ── Authenticate via the tenant's Moyasar webhook secret ─────────
    # Build the merchant's service (loads per-tenant creds) and let it
    # compare the payload's secret_token. Fails closed when no secret is
    # configured.
    try:
        svc = await get_tenant_payment_service("moyasar", order.tenant_id, db)
        verified = svc.verify_webhook_signature(payload, "")
    except Exception as e:  # pragma: no cover - defensive
        log.warning("webhook_verify_error", error=str(e))
        verified = None

    if not verified:
        log.warning("webhook_signature_invalid_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook authentication failed",
        )

    # ── RLS narrowing ────────────────────────────────────────────────
    await narrow_to_tenant(db, order.tenant_id)

    event_type = (fields["type"] or "").lower()
    is_paid = event_type in _PAID_TYPES or fields["status"] == "paid"

    if is_paid:
        log.info("payment_success")
        old_status = (
            order.status.value if hasattr(order.status, "value") else str(order.status)
        )
        order.mark_as_paid(
            payment_id=str(payment_id or ""),
            payment_method="moyasar",
        )
        await order_repo.update(order)

        # Transaction record for reconciliation
        tx = PaymentTransactionModel(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            channel="online",
            gateway="moyasar",
            display_name=(
                f"{fields['card_brand']} •••• {fields['card_last_four']}"
                if fields["card_last_four"]
                else "Card"
            ),
            amount_cents=int(fields["amount"] or 0),
            currency=fields["currency"],
            status="success",
            gateway_transaction_id=str(payment_id or ""),
            processing_completed_at=datetime.now(UTC),
        )
        db.add(tx)
        await db.flush()
        log.info("payment_transaction_created", tx_id=str(tx.id))

        # Funnel: order_completed (only on success, mirroring kashier/paymob)
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
                    "payment_method": "moyasar",
                },
            )
        except Exception:
            log.warning("funnel_event_failed", exc_info=True)

        # Meta CAPI Purchase (best-effort; event_id == order.id dedupes)
        try:
            from src.application.services.meta_capi_purchase_dispatcher import (
                enqueue_meta_capi_purchase,
            )

            await enqueue_meta_capi_purchase(db, order)
        except Exception:
            log.warning("meta_capi_purchase_enqueue_failed", exc_info=True)

        # Order status event → shipment auto-creation, notifications
        try:
            from src.core.events.order_events import OrderStatusChangedEvent
            from src.infrastructure.events.setup import get_event_bus

            store = await store_repo.get_by_id(order.store_id)
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

        # Invoice generation now that payment is confirmed
        from src.api.v1.routes.webhooks._invoice_helper import (
            generate_invoice_for_paid_order,
        )

        await generate_invoice_for_paid_order(
            db=db,
            order_id=order.id,
            store_id=order.store_id,
            tenant_id=order.tenant_id,
        )

    elif event_type in _FAILED_TYPES or fields["status"] == "failed":
        msg = fields["failure_message"] or "Payment failed"
        log.warning("payment_failed", error_message=msg)
        order.mark_payment_failed(reason=msg)
        await order_repo.update(order)
    else:
        log.info("webhook_status_no_action", status=fields["status"])

    return {"status": "received", "payment_id": payment_id}


@router.get("/redirect", operation_id="moyasar_redirect")
async def moyasar_redirect(
    order_id: str | None = Query(None),
    status: str | None = Query(None),  # noqa: A002 - matches Moyasar query param
    db: AsyncSession = Depends(get_admin_db_session),
):
    """Browser redirect target after the customer completes payment.

    The webhook is authoritative; this only routes the shopper to a
    confirmation or retry page. As a backup it marks the order paid when
    the webhook hasn't landed yet.
    """
    log = logger.bind(webhook="moyasar_redirect", order_id=order_id, status=status)
    log.info("redirect_received")

    order_repo = OrderRepository(db)
    store_repo = StoreRepository(db)

    order = None
    if order_id:
        try:
            order = await order_repo.get_by_id(UUID(str(order_id)))
        except (ValueError, AttributeError):
            order = await order_repo.get_by_payment_id_for_update(str(order_id))

    if order:
        store = await store_repo.get_by_id(order.store_id)
        if store:
            base_url = f"https://{store.subdomain}.numueg.app"
            paid = (status or "").lower() == "paid"
            if paid:
                redirect_url = (
                    f"{base_url}/order-confirmation"
                    f"?order_id={order.id}"
                    f"&order_number={order.order_number}"
                    f"&status=paid"
                    f"&total={order.total}"
                )
            else:
                redirect_url = f"{base_url}/checkout?payment_failed=true"
            return RedirectResponse(url=redirect_url)

    return RedirectResponse(url="https://numueg.app")
