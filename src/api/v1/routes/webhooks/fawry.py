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
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)

logger = get_logger(__name__)
router = APIRouter()

# Initialize services once at module level to reuse connections
fawry_service = FawryPaymentService()
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

    Payment statuses:
    - NEW: Reference created (initial)
    - PAID: Payment received
    - EXPIRED: Reference expired
    - CANCELED: Reference cancelled
    - REFUNDED: Payment refunded
    """
    payload = await request.body()
    log = logger.bind(webhook="fawry")

    # ── Security Agent: signature verification ──────────────────────
    if settings.fawry_security_key:
        verified_data = fawry_service.verify_webhook_signature(
            payload,
            x_fawry_signature or "",
        )
        if not verified_data:
            log.warning("webhook_signature_invalid")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
        data = verified_data
    else:
        # In development, accept without verification
        data = json.loads(payload)
        log.warning("webhook_no_signature_verification", mode="development")

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
):
    """Verify Fawry payment status.

    Endpoint for frontend to check if Fawry payment was completed.
    Useful when customer pays and returns to the site.

    Args:
        merchant_ref_number: Your order reference

    Returns:
        Payment status details
    """
    try:
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
    except Exception as e:
        logger.error("fawry_status_check_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify payment status",
        )
