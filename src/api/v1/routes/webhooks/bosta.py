"""Bosta webhook handler.

Receives delivery status updates from Bosta:
- Picked up
- In transit
- Out for delivery
- Delivered
- Returned
- Failed delivery attempt
"""

import json

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.external_services.bosta import BostaShippingService

logger = get_logger(__name__)
router = APIRouter()

# Initialize service
bosta_service = BostaShippingService()


@router.post("/callback")
async def bosta_callback(
    request: Request,
    x_bosta_signature: str = Header(None, alias="x-bosta-signature"),
):
    """Handle Bosta delivery status webhook.

    Bosta sends POST requests when delivery status changes.
    The x-bosta-signature header contains HMAC signature.

    Delivery states:
    - PENDING_PICKUP: Awaiting pickup
    - PICKED_UP: Collected from merchant
    - IN_WAREHOUSE: At Bosta hub
    - OUT_FOR_DELIVERY: On delivery vehicle
    - DELIVERED: Successfully delivered
    - RETURNED: Returned to merchant
    - CANCELLED: Order cancelled
    - DELIVERY_FAILED: Delivery attempt failed
    """
    payload = await request.body()
    log = logger.bind(webhook="bosta")

    # Verify signature
    if settings.bosta_webhook_secret:
        verified_data = bosta_service.verify_webhook_signature(
            payload,
            x_bosta_signature or "",
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

    # Extract delivery details
    delivery = data.get("delivery", {})
    tracking_number = delivery.get("trackingNumber")
    state = delivery.get("state", {}).get("value", "")
    business_reference = delivery.get("businessReference")
    cod_amount = delivery.get("cod", {}).get("amount")

    log = log.bind(
        tracking_number=tracking_number,
        state=state,
        business_reference=business_reference,
        cod_amount=cod_amount,
    )
    log.info("webhook_received")

    # Process based on delivery state
    if state == "DELIVERED":
        log.info("delivery_completed")
        # TODO: Update order status
        # await order_service.mark_delivered(business_reference, tracking_number)

        if cod_amount:
            log.info("cod_collected", amount=cod_amount, currency="EGP")
            # TODO: Mark COD as collected
            # await payment_service.mark_cod_collected(business_reference, cod_amount)

    elif state == "RETURNED":
        log.info("delivery_returned")
        # TODO: Update order status
        # await order_service.mark_returned(business_reference)

    elif state == "OUT_FOR_DELIVERY":
        log.info("delivery_out_for_delivery")
        # TODO: Send notification to customer
        # await notification_service.send_out_for_delivery(business_reference)

    elif state == "PICKED_UP":
        log.info("delivery_picked_up")
        # TODO: Update order to shipped
        # await order_service.mark_shipped(business_reference, tracking_number)

    elif state == "DELIVERY_FAILED":
        failure_reason = delivery.get("failedAttempt", {}).get("reason", "Unknown")
        log.warning("delivery_failed", failure_reason=failure_reason)
        # TODO: Notify merchant and possibly customer
        # await notification_service.notify_delivery_failed(business_reference, failure_reason)

    elif state == "CANCELLED":
        log.info("delivery_cancelled")
        # TODO: Update order status
        # await order_service.mark_delivery_cancelled(business_reference)

    else:
        log.debug("delivery_state_update")

    return {
        "status": "received",
        "tracking_number": tracking_number,
        "state": state,
    }


@router.get("/track/{tracking_number}")
async def track_bosta_delivery(tracking_number: str):
    """Get Bosta delivery tracking information.

    Public endpoint for customers to check delivery status.

    Args:
        tracking_number: Bosta tracking number

    Returns:
        Tracking information
    """
    log = logger.bind(tracking_number=tracking_number)

    try:
        tracking = await bosta_service.track_shipment("Bosta", tracking_number)
        log.info("tracking_retrieved", status=tracking.status)
        return {
            "tracking_number": tracking.tracking_number,
            "status": tracking.status,
            "estimated_delivery": tracking.estimated_delivery.isoformat() if tracking.estimated_delivery else None,
            "events": [
                {
                    "status": event.status,
                    "description": event.description,
                    "location": event.location,
                    "timestamp": event.timestamp.isoformat(),
                }
                for event in tracking.events
            ],
        }
    except Exception as e:
        log.exception("tracking_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get tracking information",
        )
