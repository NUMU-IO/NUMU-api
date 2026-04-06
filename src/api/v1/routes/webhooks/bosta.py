"""Bosta webhook handler.

Receives delivery status updates from Bosta:
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
from src.infrastructure.external_services.bosta import BostaShippingService
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.shipment_repository import ShipmentRepository
from src.infrastructure.tenancy.rls import narrow_to_tenant

logger = get_logger(__name__)
router = APIRouter()

# Initialize service (global fallback)
bosta_service = BostaShippingService()

# Map Bosta states to ShipmentStatus
BOSTA_STATE_MAP = {
    "PENDING_PICKUP": ShipmentStatus.CREATED,
    "PICKED_UP": ShipmentStatus.PICKED_UP,
    "IN_WAREHOUSE": ShipmentStatus.IN_TRANSIT,
    "IN_TRANSIT": ShipmentStatus.IN_TRANSIT,
    "OUT_FOR_DELIVERY": ShipmentStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ShipmentStatus.DELIVERED,
    "RETURNED": ShipmentStatus.RETURNED,
    "CANCELLED": ShipmentStatus.CANCELLED,
    "DELIVERY_FAILED": ShipmentStatus.FAILED,
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
    """Update shipment record based on Bosta state."""
    if not shipment:
        return

    new_status = BOSTA_STATE_MAP.get(state)
    if not new_status:
        return

    description = kwargs.get("description", f"Bosta state: {state}")

    if state == "DELIVERED":
        cod_amount = kwargs.get("cod_amount")
        shipment.mark_delivered(
            cod_collected=bool(cod_amount),
            cod_amount=cod_amount,
        )
    elif state == "PICKED_UP":
        shipment.mark_picked_up()
    elif state == "DELIVERY_FAILED":
        shipment.mark_failed(kwargs.get("failure_reason", ""))
    elif state == "RETURNED":
        shipment.mark_returned()
    elif state == "CANCELLED":
        shipment.mark_cancelled("Cancelled by carrier")
    elif state == "OUT_FOR_DELIVERY":
        shipment.update_status(ShipmentStatus.OUT_FOR_DELIVERY, "Out for delivery")
    elif state in ("IN_WAREHOUSE", "IN_TRANSIT"):
        shipment.update_status(ShipmentStatus.IN_TRANSIT, description)
    else:
        shipment.update_status(new_status, description)

    await shipment_repo.update(shipment)
    log.info(
        "shipment_status_updated",
        shipment_id=str(shipment.id),
        new_status=new_status.value,
    )


async def _record_network_event_from_order(
    order,
    shipment,
    event_type: str,
    session,
    log,
) -> None:
    """Record an rto/delivery event in the cross-merchant network reputation.

    COD-only and idempotent: a flag is written to ``shipment.metadata``
    after the first successful write to prevent double-counting on Bosta
    webhook replays. Fire-and-forget — never raises.
    """
    if not order or not shipment:
        return
    if not shipment.cod_amount or shipment.cod_amount <= 0:
        return

    flag_key = f"network_{event_type}_recorded"
    if shipment.metadata and shipment.metadata.get(flag_key):
        return

    try:
        phone = order.shipping_address.phone if order.shipping_address else None
        if not phone:
            return

        from src.application.services.network_reputation_service import (
            extract_phone_hash_from_string,
            write_network_event,
        )
        from src.infrastructure.repositories.shopify_repository import (
            NetworkReputationRepository,
        )

        phone_hash = extract_phone_hash_from_string(phone)
        if not phone_hash:
            return

        repo = NetworkReputationRepository(session)
        await write_network_event(
            phone_hash=phone_hash,
            store_id=order.store_id,
            event_type=event_type,
            network_repo=repo,
        )

        # Mark the shipment so we don't double-count on webhook replay.
        if shipment.metadata is None:
            shipment.metadata = {}
        shipment.metadata[flag_key] = True

        log.info(
            "network_event_recorded",
            event_type=event_type,
            store_id=str(order.store_id),
        )
    except Exception as exc:
        log.warning("network_event_failed", event_type=event_type, error=str(exc))


@router.post("/callback", operation_id="bosta_callback")
async def bosta_callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_admin_db_session)],
    x_bosta_signature: str = Header(None, alias="x-bosta-signature"),
):
    """Handle Bosta delivery status webhook.

    Bosta sends POST requests when delivery status changes.
    Uses per-store webhook secret for verification (each merchant has their own
    Bosta account). Falls back to global secret, then dev mode (no verification).
    """
    payload = await request.body()
    log = logger.bind(webhook="bosta")

    # Parse payload first to identify the store
    data = json.loads(payload)
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
    if tenant_id and x_bosta_signature:
        # Try per-store webhook secret
        try:
            from src.infrastructure.repositories.store_repository import (
                StoreRepository,
            )

            store_repo = StoreRepository(session)
            store = await store_repo.get_by_id(tenant_id)
            if store and store.settings:
                bosta_config = store.settings.get("shipping", {}).get("bosta", {})
                encrypted_creds = bosta_config.get("encrypted_credentials")
                key_id = bosta_config.get("encryption_key_id")
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
                        store_bosta = BostaShippingService(
                            webhook_secret=store_webhook_secret
                        )
                        verified = store_bosta.verify_webhook_signature(
                            payload, x_bosta_signature
                        )
                        if verified:
                            signature_verified = True
                            log.info("webhook_verified_per_store")
                        else:
                            log.warning("webhook_per_store_signature_invalid")
        except Exception as e:
            log.warning("webhook_per_store_verify_error", error=str(e))

    if not signature_verified and x_bosta_signature:
        # Fallback to global secret
        if settings.bosta_webhook_secret:
            verified_data = bosta_service.verify_webhook_signature(
                payload, x_bosta_signature
            )
            if verified_data:
                signature_verified = True
                log.info("webhook_verified_global")
            else:
                log.warning("webhook_global_signature_invalid")

    if not signature_verified:
        if x_bosta_signature:
            # Signature was provided but couldn't be verified
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

                    # Emit funnel event: order_delivered
                    try:
                        from src.infrastructure.repositories.funnel_event_repository import (
                            FunnelEventRepository,
                        )

                        fe_repo = FunnelEventRepository(session)
                        await fe_repo.create(
                            tenant_id=tenant_id or order.tenant_id,
                            store_id=order.store_id,
                            step="order_delivered",
                            customer_id=order.customer_id,
                            step_data={
                                "order_id": str(order.id),
                                "tracking_number": tracking_number,
                            },
                        )
                    except Exception:
                        pass

                if cod_amount and not order.is_paid:
                    order.mark_as_paid(
                        payment_id=f"cod-bosta-{tracking_number}",
                        payment_method="cod",
                    )
                    order.metadata["cod_amount"] = cod_amount
                    order.metadata["cod_collected_via"] = "bosta_webhook"
                    order.metadata["cod_tracking_number"] = tracking_number
                    log.info("cod_collected", amount=cod_amount, order_id=str(order.id))

                await order_repo.update(order)
            except Exception as e:
                log.error("delivery_order_update_failed", error=str(e))

        await _update_shipment_status(
            shipment, shipment_repo, state, log, cod_amount=cod_amount
        )

        # Record positive network event for the customer (COD only).
        # Idempotent — replayed webhooks won't double-count.
        await _record_network_event_from_order(
            order, shipment, "delivery", session, log
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
                    order.cancel(reason="Returned by carrier (Bosta)")
                elif order.status == OrderStatus.SHIPPED:
                    order.status = OrderStatus.CANCELLED
                    order.metadata.setdefault("status_history", []).append({
                        "from": OrderStatus.SHIPPED.value,
                        "to": OrderStatus.CANCELLED.value,
                        "reason": "Returned by carrier (Bosta)",
                    })
                    order.touch()
                await order_repo.update(order)
                log.info("order_cancelled_return", order_id=str(order.id))
            except Exception as e:
                log.error("return_order_update_failed", error=str(e))

        # Track COD rejection for returned COD shipments
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

        # Record RTO event in cross-merchant trust network (COD only,
        # idempotent). Only RETURNED counts as RTO — DELIVERY_FAILED may
        # be retried by the carrier.
        await _record_network_event_from_order(order, shipment, "rto", session, log)

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

    elif state in ("IN_WAREHOUSE", "IN_TRANSIT"):
        log.info("delivery_in_transit")
        await _update_shipment_status(shipment, shipment_repo, state, log)
        try:
            await session.commit()
        except Exception as e:
            log.error("transit_commit_failed", error=str(e))
            await session.rollback()

    elif state == "DELIVERY_FAILED":
        failure_reason = delivery.get("failedAttempt", {}).get("reason", "Unknown")
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

        # Track COD rejection in shipment extra_data
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
                    order.cancel(reason="Cancelled via Bosta")
                    await order_repo.update(order)
                    log.info("order_cancelled_bosta", order_id=str(order.id))
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
        # Try to update shipment for any unknown state too
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


@router.get("/track/{tracking_number}", operation_id="track_bosta_delivery")
async def track_bosta_delivery(tracking_number: str):
    """Get Bosta delivery tracking information.

    Public endpoint for customers to check delivery status.
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
