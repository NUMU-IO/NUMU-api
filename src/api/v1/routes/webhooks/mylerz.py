"""Mylerz webhook handler.

Receives delivery status updates from Mylerz:
- Picked up
- In transit
- Out for delivery
- Delivered (+ COD payment confirmation)
- Returned
- Failed delivery attempt

Updates BOTH the Order and Shipment records.
"""

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.config.logging_config import get_logger
from src.core.entities.order import OrderStatus
from src.core.entities.shipment import ShipmentStatus
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.external_services.mylerz import MylerzShippingService
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.shipment_repository import ShipmentRepository
from src.infrastructure.tenancy.rls import narrow_to_tenant

logger = get_logger(__name__)
router = APIRouter()

# Initialize service (global fallback)
mylerz_service = MylerzShippingService()

# Map Mylerz states to ShipmentStatus
MYLERZ_STATE_MAP = {
    "PICKED_UP": ShipmentStatus.PICKED_UP,
    "IN_TRANSIT": ShipmentStatus.IN_TRANSIT,
    "OUT_FOR_DELIVERY": ShipmentStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ShipmentStatus.DELIVERED,
    "RETURNED": ShipmentStatus.RETURNED,
    "FAILED": ShipmentStatus.FAILED,
    "CANCELLED": ShipmentStatus.CANCELLED,
}


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


async def _find_shipment(repo: ShipmentRepository, tracking_number: str, log):
    """Look up shipment by tracking number with row-level lock."""
    if not tracking_number:
        return None
    try:
        shipment = await repo.get_by_tracking_number_for_update(tracking_number)
        if not shipment:
            log.debug(
                "shipment_not_found_for_tracking", tracking_number=tracking_number
            )
        return shipment
    except Exception as e:
        log.warning("shipment_lookup_failed", error=str(e))
        return None


async def _update_shipment_status(shipment, shipment_repo, state: str, log, **kwargs):
    """Update shipment record based on Mylerz state."""
    if not shipment:
        return

    new_status = MYLERZ_STATE_MAP.get(state)
    if not new_status:
        return

    description = kwargs.get("description", f"Mylerz state: {state}")

    if state == "DELIVERED":
        cod_amount = kwargs.get("cod_amount")
        shipment.mark_delivered(
            cod_collected=bool(cod_amount),
            cod_amount=cod_amount,
        )
    elif state == "PICKED_UP":
        shipment.mark_picked_up()
    elif state == "FAILED":
        shipment.mark_failed(kwargs.get("failure_reason", ""))
    elif state == "RETURNED":
        shipment.mark_returned()
    elif state == "CANCELLED":
        shipment.mark_cancelled("Cancelled by carrier")
    elif state == "OUT_FOR_DELIVERY":
        shipment.update_status(ShipmentStatus.OUT_FOR_DELIVERY, "Out for delivery")
    elif state == "IN_TRANSIT":
        shipment.update_status(ShipmentStatus.IN_TRANSIT, description)
    else:
        shipment.update_status(new_status, description)

    await shipment_repo.update(shipment)
    log.info(
        "shipment_status_updated",
        shipment_id=str(shipment.id),
        new_status=new_status.value,
    )


@router.post("/callback", operation_id="mylerz_callback")
async def mylerz_callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_admin_db_session)],
    x_mylerz_signature: str = Header(None, alias="x-mylerz-signature"),
):
    """Handle Mylerz delivery status webhook.

    Mylerz sends POST requests when delivery status changes.
    Uses per-store webhook secret for verification (each merchant has their own
    Mylerz account). Falls back to global secret, then dev mode (no verification).
    """
    payload = await request.body()
    log = logger.bind(webhook="mylerz")

    # Parse payload to identify the shipment
    data = json.loads(payload)
    tracking_number = (
        data.get("Barcode") or data.get("barcode") or data.get("tracking_number", "")
    )
    state = data.get("Status") or data.get("status", "")
    cod_amount = data.get("CODAmount") or data.get("cod_amount")
    reference = data.get("Reference") or data.get("reference", "")

    log = log.bind(
        tracking_number=tracking_number,
        state=state,
        reference=reference,
        cod_amount=cod_amount,
    )
    log.info("webhook_received")

    order_repo = OrderRepository(session)
    shipment_repo = ShipmentRepository(session)

    # Look up shipment and order to identify the store
    order = await _find_order(order_repo, tracking_number, log)
    shipment = await _find_shipment(shipment_repo, tracking_number, log)

    # Determine tenant_id from whichever we found
    tenant_id = None
    if shipment:
        tenant_id = shipment.tenant_id
    elif order:
        tenant_id = order.tenant_id

    # Verify signature using per-store secret, then global fallback
    signature_verified = False
    if tenant_id and x_mylerz_signature:
        try:
            from src.infrastructure.repositories.store_repository import (
                StoreRepository,
            )

            store_repo = StoreRepository(session)
            store = await store_repo.get_by_id(tenant_id)
            if store and store.settings:
                mylerz_config = store.settings.get("shipping", {}).get("mylerz", {})
                encrypted_creds = mylerz_config.get("encrypted_credentials")
                key_id = mylerz_config.get("encryption_key_id")
                if encrypted_creds and key_id:
                    import base64

                    from src.infrastructure.external_services.secrets.secrets_manager import (
                        get_secrets_manager,
                    )

                    secrets = get_secrets_manager()
                    cred_data = await secrets.decrypt(
                        base64.b64decode(encrypted_creds), key_id
                    )
                    store_webhook_secret = cred_data.get("webhook_secret")
                    if store_webhook_secret:
                        store_mylerz = MylerzShippingService(
                            webhook_secret=store_webhook_secret
                        )
                        verified = store_mylerz.verify_webhook_signature(
                            payload, x_mylerz_signature
                        )
                        if verified:
                            signature_verified = True
                            log.info("webhook_verified_per_store")
                        else:
                            log.warning("webhook_per_store_signature_invalid")
        except Exception as e:
            log.warning("webhook_per_store_verify_error", error=str(e))

    if not signature_verified and x_mylerz_signature:
        if settings.mylerz_webhook_secret:
            verified_data = mylerz_service.verify_webhook_signature(
                payload, x_mylerz_signature
            )
            if verified_data:
                signature_verified = True
                log.info("webhook_verified_global")
            else:
                log.warning("webhook_global_signature_invalid")

    if not signature_verified:
        if x_mylerz_signature:
            log.warning("webhook_signature_unverified_accepting", mode="permissive")
        else:
            log.warning("webhook_no_signature", mode="development")

    if tenant_id:
        await narrow_to_tenant(session, tenant_id)

    # Process based on delivery state
    if state == "DELIVERED":
        log.info("delivery_completed")
        if order:
            try:
                if order.status == OrderStatus.SHIPPED:
                    order.deliver()
                    log.info("order_marked_delivered", order_id=str(order.id))

                if cod_amount and not order.is_paid:
                    order.mark_as_paid(
                        payment_id=f"cod-mylerz-{tracking_number}",
                        payment_method="cod",
                    )
                    order.metadata["cod_amount"] = cod_amount
                    order.metadata["cod_collected_via"] = "mylerz_webhook"
                    order.metadata["cod_tracking_number"] = tracking_number
                    log.info("cod_collected", amount=cod_amount, order_id=str(order.id))

                await order_repo.update(order)
            except Exception as e:
                log.error("delivery_order_update_failed", error=str(e))

        await _update_shipment_status(
            shipment, shipment_repo, state, log, cod_amount=cod_amount
        )

        try:
            await session.commit()
        except Exception as e:
            log.error("delivery_commit_failed", error=str(e))
            await session.rollback()

    elif state == "PICKED_UP":
        log.info("delivery_picked_up")
        if order:
            try:
                if order.status == OrderStatus.PROCESSING:
                    order.ship(tracking_number=tracking_number)
                    await order_repo.update(order)
                    log.info("order_marked_shipped", order_id=str(order.id))
                elif order.status == OrderStatus.CONFIRMED:
                    order.start_processing()
                    order.ship(tracking_number=tracking_number)
                    await order_repo.update(order)
                    log.info("order_confirmed_and_shipped", order_id=str(order.id))
            except Exception as e:
                log.error("pickup_order_update_failed", error=str(e))

        await _update_shipment_status(shipment, shipment_repo, state, log)

        try:
            await session.commit()
        except Exception as e:
            log.error("pickup_commit_failed", error=str(e))
            await session.rollback()

    elif state == "RETURNED":
        log.info("delivery_returned")
        if order:
            try:
                if order.can_be_cancelled:
                    order.cancel(reason="Returned by carrier (Mylerz)")
                elif order.status == OrderStatus.SHIPPED:
                    order.status = OrderStatus.CANCELLED
                    order.metadata.setdefault("status_history", []).append({
                        "from": OrderStatus.SHIPPED.value,
                        "to": OrderStatus.CANCELLED.value,
                        "reason": "Returned by carrier (Mylerz)",
                    })
                    order.touch()
                await order_repo.update(order)
                log.info("order_cancelled_return", order_id=str(order.id))
            except Exception as e:
                log.error("return_order_update_failed", error=str(e))

        if shipment and shipment.cod_amount and shipment.cod_amount > 0:
            if shipment.metadata is None:
                shipment.metadata = {}
            shipment.metadata["cod_rejected"] = True
            shipment.metadata["rejection_reason"] = "Returned by carrier"
            shipment.metadata["rejection_timestamp"] = datetime.now(UTC).isoformat()
            log.info(
                "cod_rejection_tracked_return",
                shipment_id=str(shipment.id),
                cod_amount=shipment.cod_amount,
            )

        await _update_shipment_status(shipment, shipment_repo, state, log)

        try:
            await session.commit()
        except Exception as e:
            log.error("return_commit_failed", error=str(e))
            await session.rollback()

    elif state == "OUT_FOR_DELIVERY":
        log.info("delivery_out_for_delivery")
        await _update_shipment_status(shipment, shipment_repo, state, log)
        try:
            await session.commit()
        except Exception as e:
            log.error("ofd_commit_failed", error=str(e))
            await session.rollback()

    elif state == "IN_TRANSIT":
        log.info("delivery_in_transit")
        await _update_shipment_status(shipment, shipment_repo, state, log)
        try:
            await session.commit()
        except Exception as e:
            log.error("transit_commit_failed", error=str(e))
            await session.rollback()

    elif state == "FAILED":
        failure_reason = data.get("FailureReason") or data.get(
            "failure_reason", "Unknown"
        )
        log.warning("delivery_failed", failure_reason=failure_reason)
        if order:
            try:
                if "delivery_failures" not in order.metadata:
                    order.metadata["delivery_failures"] = []
                order.metadata["delivery_failures"].append({
                    "reason": failure_reason,
                    "tracking_number": tracking_number,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
                await order_repo.update(order)
            except Exception as e:
                log.error("failure_order_update_failed", error=str(e))

        if shipment and shipment.cod_amount and shipment.cod_amount > 0:
            if shipment.metadata is None:
                shipment.metadata = {}
            shipment.metadata["cod_rejected"] = True
            shipment.metadata["rejection_reason"] = failure_reason
            shipment.metadata["rejection_timestamp"] = datetime.now(UTC).isoformat()
            log.info(
                "cod_rejection_tracked",
                shipment_id=str(shipment.id),
                cod_amount=shipment.cod_amount,
                reason=failure_reason,
            )

        await _update_shipment_status(
            shipment, shipment_repo, state, log, failure_reason=failure_reason
        )

        try:
            await session.commit()
        except Exception as e:
            log.error("failure_commit_failed", error=str(e))
            await session.rollback()

    elif state == "CANCELLED":
        log.info("delivery_cancelled")
        if order:
            try:
                if order.can_be_cancelled:
                    order.cancel(reason="Cancelled via Mylerz")
                    await order_repo.update(order)
                    log.info("order_cancelled_mylerz", order_id=str(order.id))
            except Exception as e:
                log.error("cancel_order_update_failed", error=str(e))

        await _update_shipment_status(shipment, shipment_repo, state, log)

        try:
            await session.commit()
        except Exception as e:
            log.error("cancel_commit_failed", error=str(e))
            await session.rollback()

    else:
        log.debug("delivery_state_update", state=state)
        await _update_shipment_status(
            shipment, shipment_repo, state, log, description=f"Unknown state: {state}"
        )
        try:
            await session.commit()
        except Exception:
            await session.rollback()

    return {
        "status": "received",
        "tracking_number": tracking_number,
        "state": state,
    }


@router.get("/track/{tracking_number}", operation_id="track_mylerz_delivery")
async def track_mylerz_delivery(tracking_number: str):
    """Get Mylerz delivery tracking information.

    Public endpoint for customers to check delivery status.
    """
    log = logger.bind(tracking_number=tracking_number)

    try:
        tracking = await mylerz_service.track_shipment("Mylerz", tracking_number)
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
                    "timestamp": event.timestamp.isoformat()
                    if hasattr(event.timestamp, "isoformat")
                    else str(event.timestamp),
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
