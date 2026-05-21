"""Fawry webhook handler.

Receives payment notifications from Fawry when:
- Customer pays at Fawry outlet
- Customer pays via Fawry app/online
- Payment reference expires
- Refund is processed

Agent collaboration:
- Security Agent: signature verification + replay protection
- DB Agent: order lookup & status persistence
- Payment Agent: state transitions (paid, expired, failed, refunded)
- Inventory Agent: stock release on expiry
- Messaging Agent: WhatsApp notifications on cancellation
- Audit Agent: event logging for every status change

Multi-tenant flow:
1. Parse payload → extract merchantRefNum
2. Resolve order by payment_id → fetch associated store
3. Decrypt store's Fawry security_key
4. Verify x-fawry-signature using the merchant's specific key
5. Process payment status via FawryWebhookService
"""

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.fawry_webhook_service import FawryWebhookService
from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.external_services.fawry import FawryPaymentService
from src.infrastructure.external_services.fawry.payment_service import (
    get_merchant_fawry_credentials,
)
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.store_repository import StoreRepository

logger = get_logger(__name__)
router = APIRouter()

_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)
_messaging_service: WhatsAppMessagingService | None = (
    WhatsAppMessagingService() if settings.whatsapp_enabled else None
)


@router.post("/callback", operation_id="fawry_callback")
async def fawry_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    x_fawry_signature: str = Header(None, alias="x-fawry-signature"),
):
    """Handle Fawry payment notification.

    Fawry sends a POST request when payment status changes.
    The x-fawry-signature header contains the SHA-256 signature.

    Uses an admin DB session (RLS bypass) because webhooks are
    system-level events with no tenant context in the HTTP request.
    Tenant isolation is enforced at the application level via explicit
    tenant_id filters on every write query.

    Multi-tenant: resolves the order from merchantRefNum, fetches the
    associated store's Fawry credentials, and verifies the signature
    using the merchant's specific security key.

    Payment statuses:
    - NEW: Reference created (initial)
    - PAID: Payment received
    - EXPIRED: Reference expired
    - CANCELED: Reference cancelled
    - REFUNDED: Payment refunded
    """
    payload = await request.body()
    log = logger.bind(webhook="fawry")

    # ── Parse payload first to resolve the merchant ──────────────────
    data = json.loads(payload)

    # Extract notification details
    reference_number = data.get("referenceNumber")
    merchant_ref_number = data.get("merchantRefNum")
    order_status = data.get("orderStatus")
    payment_amount = data.get("paymentAmount", 0)
    payment_method = data.get("paymentMethod")
    fawry_fees = data.get("fawryFees", 0)

    log = log.bind(
        reference_number=reference_number,
        merchant_ref=merchant_ref_number,
        order_status=order_status,
        payment_amount=payment_amount,
        payment_method=payment_method,
    )
    log.info("webhook_received")

    # ── Per-merchant signature verification ──────────────────────────
    # Resolve the order → store → decrypt store's Fawry security key
    order_repo = OrderRepository(db)
    store_repo = StoreRepository(db)

    order = None
    if merchant_ref_number:
        order = await order_repo.get_by_payment_id_for_update(merchant_ref_number)

    if order:
        store = await store_repo.get_by_id(order.store_id)
        if store:
            try:
                creds = await get_merchant_fawry_credentials(store.settings)
                fawry_service = FawryPaymentService(
                    merchant_code=creds["merchant_code"],
                    security_key=creds["security_key"],
                )
                verified_data = fawry_service.verify_webhook_signature(
                    payload, x_fawry_signature or ""
                )
                if not verified_data:
                    log.warning("webhook_signature_invalid")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid webhook signature",
                    )
                data = verified_data
            except HTTPException:
                raise
            except Exception as e:
                log.warning("webhook_fawry_creds_resolution_failed", error=str(e))
                if not settings.debug:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not verify webhook signature",
                    )
        else:
            log.warning("webhook_store_not_found", store_id=str(order.store_id))
    else:
        # Order not found — cannot resolve merchant credentials.
        # In development, continue without verification for debugging.
        if not settings.debug:
            log.warning(
                "webhook_order_not_found_for_signature", ref=merchant_ref_number
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Cannot verify webhook: order not found",
            )
        log.warning("webhook_no_signature_verification", mode="development")

    # ── Build service (reuses module-level cache/messaging singletons) ──
    webhook_service = FawryWebhookService(
        db=db, cache=_cache_service, messaging=_messaging_service
    )

    # ── Security Agent: replay protection ───────────────────────────
    if reference_number and await webhook_service.check_replay(reference_number):
        log.warning("webhook_duplicate_rejected")
        return {
            "status": "duplicate",
            "reference_number": reference_number,
            "order_status": order_status,
        }

    # ── Security Agent: timestamp freshness ─────────────────────────
    if not webhook_service.check_timestamp(data):
        log.warning("webhook_timestamp_stale")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook timestamp too old",
        )

    # ── Process based on status ─────────────────────────────────────
    if order_status == "PAID":
        await webhook_service.handle_paid(
            merchant_ref=merchant_ref_number,
            reference_number=reference_number,
            payment_amount=payment_amount,
            payment_method=payment_method,
            fawry_fees=fawry_fees,
            raw_data=data,
        )

    elif order_status == "EXPIRED":
        await webhook_service.handle_expired(
            merchant_ref=merchant_ref_number,
            raw_data=data,
        )

    elif order_status == "CANCELED":
        await webhook_service.handle_canceled(
            merchant_ref=merchant_ref_number,
            raw_data=data,
        )

    elif order_status == "REFUNDED":
        await webhook_service.handle_refunded(
            merchant_ref=merchant_ref_number,
            payment_amount=payment_amount,
            raw_data=data,
        )

    elif order_status == "NEW":
        # Initial reference creation — no action needed
        log.debug("webhook_new_reference")

    else:
        log.warning("webhook_unknown_status")

    # Always return 200 to acknowledge receipt
    return {
        "status": "received",
        "reference_number": reference_number,
        "order_status": order_status,
    }


@router.get("/verify", operation_id="fawry_verify_payment")
async def fawry_verify_payment(
    merchant_ref_number: str,
    store_id: str,
    db: AsyncSession = Depends(get_admin_db_session),
):
    """Verify Fawry payment status.

    Endpoint for frontend to check if Fawry payment was completed.
    Useful when customer pays and returns to the site.

    Uses the store's own Fawry credentials to query payment status.

    Args:
        merchant_ref_number: Your order reference
        store_id: Store ID to resolve credentials

    Returns:
        Payment status details
    """
    from uuid import UUID

    store_repo = StoreRepository(db)

    try:
        store = await store_repo.get_by_id(UUID(store_id))
        if not store:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Store not found",
            )

        creds = await get_merchant_fawry_credentials(store.settings)
        fawry_service = FawryPaymentService(
            merchant_code=creds["merchant_code"],
            security_key=creds["security_key"],
        )

        status_data = await fawry_service.get_payment_status_details(
            merchant_ref_number
        )
        return {
            "success": status_data.get("paymentStatus") == "PAID",
            "status": status_data.get("paymentStatus"),
            "reference_number": status_data.get("referenceNumber"),
            "payment_amount": status_data.get("paymentAmount"),
            "payment_method": status_data.get("paymentMethod"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("fawry_status_check_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify payment status",
        )
