"""Fawry webhook handler.

Receives payment notifications from Fawry when:
- Customer pays at Fawry outlet
- Customer pays via Fawry app/online
- Payment reference expires
- Refund is processed
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.config import settings
from src.infrastructure.external_services.fawry import FawryPaymentService

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize service
fawry_service = FawryPaymentService()


@router.post("/callback")
async def fawry_callback(
    request: Request,
    x_fawry_signature: str = Header(None, alias="x-fawry-signature"),
):
    """Handle Fawry payment notification.

    Fawry sends a POST request when payment status changes.
    The x-fawry-signature header contains the SHA-256 signature.

    Payment statuses:
    - NEW: Reference created (initial)
    - PAID: Payment received
    - EXPIRED: Reference expired
    - CANCELED: Reference cancelled
    - REFUNDED: Payment refunded
    """
    payload = await request.body()

    # Verify signature
    if settings.fawry_security_key:
        verified_data = fawry_service.verify_webhook_signature(
            payload,
            x_fawry_signature or "",
        )
        if not verified_data:
            logger.warning("Fawry webhook signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
        data = verified_data
    else:
        # In development, accept without verification
        import json
        data = json.loads(payload)
        logger.warning("Fawry webhook received without signature verification (dev mode)")

    # Extract notification details
    reference_number = data.get("referenceNumber")
    merchant_ref_number = data.get("merchantRefNum")
    order_status = data.get("orderStatus")
    payment_amount = data.get("paymentAmount", 0)
    payment_method = data.get("paymentMethod")
    fawry_fees = data.get("fawryFees", 0)

    logger.info(
        f"Fawry webhook: ref={reference_number}, merchant_ref={merchant_ref_number}, "
        f"status={order_status}, amount={payment_amount}, method={payment_method}"
    )

    # Process based on status
    if order_status == "PAID":
        logger.info(
            f"Fawry payment received for {merchant_ref_number}: "
            f"{payment_amount} EGP (fees: {fawry_fees})"
        )
        # TODO: Update order status in database
        # await order_service.mark_paid(
        #     merchant_ref_number,
        #     reference_number,
        #     int(payment_amount * 100),  # Convert to cents
        #     fawry_fees=int(fawry_fees * 100) if fawry_fees else 0,
        # )

    elif order_status == "EXPIRED":
        logger.info(f"Fawry reference expired for {merchant_ref_number}")
        # TODO: Update order status in database
        # await order_service.mark_payment_expired(merchant_ref_number)

    elif order_status == "CANCELED":
        logger.info(f"Fawry reference cancelled for {merchant_ref_number}")
        # TODO: Update order status in database
        # await order_service.mark_cancelled(merchant_ref_number)

    elif order_status == "REFUNDED":
        logger.info(f"Fawry refund processed for {merchant_ref_number}")
        # TODO: Update order status in database
        # await order_service.mark_refunded(merchant_ref_number, int(payment_amount * 100))

    elif order_status == "NEW":
        # Initial reference creation - usually no action needed
        logger.debug(f"Fawry reference created: {reference_number}")

    else:
        logger.warning(f"Unknown Fawry status: {order_status}")

    # Always return 200 to acknowledge receipt
    return {
        "status": "received",
        "reference_number": reference_number,
        "order_status": order_status,
    }


@router.get("/verify")
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
        status_data = await fawry_service.get_payment_status_details(merchant_ref_number)
        return {
            "success": status_data.get("paymentStatus") == "PAID",
            "status": status_data.get("paymentStatus"),
            "reference_number": status_data.get("referenceNumber"),
            "payment_amount": status_data.get("paymentAmount"),
            "payment_method": status_data.get("paymentMethod"),
        }
    except Exception as e:
        logger.error(f"Fawry status check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify payment status",
        )
