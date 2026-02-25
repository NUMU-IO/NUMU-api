"""Bosta webhook handler.

Receives delivery status updates from Bosta:
- Picked up
- In transit
- Out for delivery
- Delivered (+ COD payment confirmation)
- Returned
- Failed delivery attempt
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db
from src.config import settings
from src.config.logging_config import get_logger
from src.core.entities.order import OrderStatus
from src.infrastructure.external_services.bosta import BostaShippingService
from src.infrastructure.repositories.order_repository import OrderRepository

logger = get_logger(__name__)
router = APIRouter()

# Initialize service
bosta_service = BostaShippingService()


async def _find_order(repo: OrderRepository, tracking_number: str, log):
    """Look up order by tracking number with row-level lock, return None on miss."""
    if not tracking_number:
        return None
    try:
        order = await repo.get_by_tracking_number_for_update(tracking_number)
        if not order:
            log.debug("order_not_found_for_tracking", tracking_number=tracking_number)
        return order
    except Exception as e:
        log.warning("order_lookup_failed", error=str(e))
        return None


@router.post("/callback", operation_id="bosta_callback")
async def bosta_callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
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

    order_repo = OrderRepository(session)

    # Process based on delivery state
    if state == "DELIVERED":
        log.info("delivery_completed")
        order = await _find_order(order_repo, tracking_number, log)
        if order:
            try:
                if order.status == OrderStatus.SHIPPED:
                    order.deliver()
                    log.info("order_marked_delivered", order_id=str(order.id))

                if cod_amount and not order.is_paid:
                    order.mark_as_paid(
                        payment_id=f"cod-bosta-{tracking_number}",
                        payment_method="cod",
                    )
                    order.metadata["cod_amount"] = cod_amount
                    order.metadata["cod_collected_via"] = "bosta_webhook"
                    order.metadata["cod_tracking_number"] = tracking_number
                    log.info(
                        "cod_collected",
                        amount=cod_amount,
                        currency="EGP",
                        order_id=str(order.id),
                    )

                await order_repo.update(order)
                await session.commit()
            except Exception as e:
                log.error("delivery_processing_failed", error=str(e))
                await session.rollback()

    elif state == "PICKED_UP":
        log.info("delivery_picked_up")
        order = await _find_order(order_repo, tracking_number, log)
        if order:
            try:
                if order.status == OrderStatus.PROCESSING:
                    order.ship(tracking_number=tracking_number)
                    await order_repo.update(order)
                    await session.commit()
                    log.info("order_marked_shipped", order_id=str(order.id))
                elif order.status == OrderStatus.CONFIRMED:
                    order.start_processing()
                    order.ship(tracking_number=tracking_number)
                    await order_repo.update(order)
                    await session.commit()
                    log.info("order_confirmed_and_shipped", order_id=str(order.id))
            except Exception as e:
                log.error("pickup_processing_failed", error=str(e))
                await session.rollback()

    elif state == "RETURNED":
        log.info("delivery_returned")
        order = await _find_order(order_repo, tracking_number, log)
        if order:
            try:
                if order.can_be_cancelled:
                    order.cancel(reason="Returned by carrier (Bosta)")
                elif order.status == OrderStatus.SHIPPED:
                    # SHIPPED -> CANCELLED is not in the standard transition map,
                    # but carrier returns are a valid external event. Use
                    # transition_to with explicit reason to record in metadata.
                    order.status = OrderStatus.CANCELLED
                    order.metadata.setdefault("status_history", []).append({
                        "from": OrderStatus.SHIPPED.value,
                        "to": OrderStatus.CANCELLED.value,
                        "reason": "Returned by carrier (Bosta)",
                    })
                    order.touch()
                await order_repo.update(order)
                await session.commit()
                log.info("order_cancelled_return", order_id=str(order.id))
            except Exception as e:
                log.error("return_processing_failed", error=str(e))
                await session.rollback()

    elif state == "OUT_FOR_DELIVERY":
        log.info("delivery_out_for_delivery")

    elif state == "DELIVERY_FAILED":
        failure_reason = delivery.get("failedAttempt", {}).get("reason", "Unknown")
        log.warning("delivery_failed", failure_reason=failure_reason)
        order = await _find_order(order_repo, tracking_number, log)
        if order:
            try:
                if "delivery_failures" not in order.metadata:
                    order.metadata["delivery_failures"] = []
                order.metadata["delivery_failures"].append({
                    "reason": failure_reason,
                    "tracking_number": tracking_number,
                })
                await order_repo.update(order)
                await session.commit()
            except Exception as e:
                log.error("failure_recording_failed", error=str(e))
                await session.rollback()

    elif state == "CANCELLED":
        log.info("delivery_cancelled")
        order = await _find_order(order_repo, tracking_number, log)
        if order:
            try:
                if order.can_be_cancelled:
                    order.cancel(reason="Cancelled via Bosta")
                    await order_repo.update(order)
                    await session.commit()
                    log.info("order_cancelled_bosta", order_id=str(order.id))
            except Exception as e:
                log.error("cancel_processing_failed", error=str(e))
                await session.rollback()

    else:
        log.debug("delivery_state_update")

    return {
        "status": "received",
        "tracking_number": tracking_number,
        "state": state,
    }


@router.get("/track/{tracking_number}", operation_id="track_bosta_delivery")
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
            "estimated_delivery": tracking.estimated_delivery.isoformat()
            if tracking.estimated_delivery
            else None,
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
