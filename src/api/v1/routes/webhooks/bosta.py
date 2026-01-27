"""Bosta webhook handler.

Receives delivery status updates from Bosta:
- Picked up
- In transit
- Out for delivery
- Delivered
- Returned
- Failed delivery attempt
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.config import settings
from src.infrastructure.external_services.bosta import BostaShippingService

logger = logging.getLogger(__name__)
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

    # Verify signature
    if settings.bosta_webhook_secret:
        verified_data = bosta_service.verify_webhook_signature(
            payload,
            x_bosta_signature or "",
        )
        if not verified_data:
            logger.warning("Bosta webhook signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
        data = verified_data
    else:
        # In development, accept without verification
        import json
        data = json.loads(payload)
        logger.warning("Bosta webhook received without signature verification (dev mode)")

    # Extract delivery details
    delivery = data.get("delivery", {})
    tracking_number = delivery.get("trackingNumber")
    state = delivery.get("state", {}).get("value", "")
    business_reference = delivery.get("businessReference")
    cod_amount = delivery.get("cod", {}).get("amount")

    logger.info(
        f"Bosta webhook: tracking={tracking_number}, state={state}, "
        f"order={business_reference}"
    )

    # Process based on delivery state
    if state == "DELIVERED":
        logger.info(f"Bosta delivery completed: {tracking_number}")
        # TODO: Update order status
        # await order_service.mark_delivered(business_reference, tracking_number)

        if cod_amount:
            logger.info(f"COD amount collected: {cod_amount} EGP")
            # TODO: Mark COD as collected
            # await payment_service.mark_cod_collected(business_reference, cod_amount)

    elif state == "RETURNED":
        logger.info(f"Bosta delivery returned: {tracking_number}")
        # TODO: Update order status
        # await order_service.mark_returned(business_reference)

    elif state == "OUT_FOR_DELIVERY":
        logger.info(f"Bosta out for delivery: {tracking_number}")
        # TODO: Send notification to customer
        # await notification_service.send_out_for_delivery(business_reference)

    elif state == "PICKED_UP":
        logger.info(f"Bosta picked up: {tracking_number}")
        # TODO: Update order to shipped
        # await order_service.mark_shipped(business_reference, tracking_number)

    elif state == "DELIVERY_FAILED":
        failure_reason = delivery.get("failedAttempt", {}).get("reason", "Unknown")
        logger.warning(f"Bosta delivery failed: {tracking_number}, reason: {failure_reason}")
        # TODO: Notify merchant and possibly customer
        # await notification_service.notify_delivery_failed(business_reference, failure_reason)

    elif state == "CANCELLED":
        logger.info(f"Bosta delivery cancelled: {tracking_number}")
        # TODO: Update order status
        # await order_service.mark_delivery_cancelled(business_reference)

    else:
        logger.debug(f"Bosta state update: {tracking_number} -> {state}")

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
    try:
        tracking = await bosta_service.track_shipment("Bosta", tracking_number)
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
        logger.error(f"Bosta tracking failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get tracking information",
        )
