"""Auto-create a carrier shipment for new orders.

Two triggers, one booking path:

* **Order created**: cash-on-delivery orders book straight away, unless the
  store asks customers to confirm COD orders on WhatsApp (they wait for the
  tap) or the order still waits for a deposit.
* **Order confirmed / processing**: everything else, i.e. confirmed COD
  orders and online-payment orders once the payment lands.

The carrier is the first registry carrier (Bosta first) the store connected
with auto-create switched on. An order with an active shipment is never
booked twice.
"""

from datetime import UTC, datetime
from uuid import UUID

from src.core.entities.shipment import Shipment, ShipmentStatus
from src.core.events.order_events import OrderCreatedEvent, OrderStatusChangedEvent
from src.core.logging import get_logger

logger = get_logger(__name__)

COD_METHODS = ("cod", "cash_on_delivery")


def auto_create_carrier(store_settings: dict | None) -> str | None:
    """First carrier with auto-create on that the store can actually book with."""
    from src.application.services.carrier_credentials import has_credentials
    from src.application.services.carrier_resolver import SUPPORTED_CARRIERS

    shipping = (store_settings or {}).get("shipping", {})
    return next(
        (
            slug
            for slug in SUPPORTED_CARRIERS
            if shipping.get(slug, {}).get("auto_create_shipment")
            and (
                shipping.get(slug, {}).get("enabled")
                or has_credentials(store_settings, slug)
            )
        ),
        None,
    )


def books_on_creation(
    store_settings: dict | None, payment_method: str | None, status: str
) -> bool:
    if (payment_method or "").lower() not in COD_METHODS:
        return False
    if status not in ("pending", "confirmed", "processing"):
        return False
    notifications = (store_settings or {}).get("whatsapp_notifications", {}) or {}
    awaits_tap = status == "pending" and notifications.get(
        "require_order_confirmation", False
    )
    return not awaits_tap


async def handle_order_created_for_shipment(event: OrderCreatedEvent) -> None:
    await _auto_create(event.order_id, event.store_id, trigger="created")


async def handle_order_status_for_shipment(event: OrderStatusChangedEvent) -> None:
    if event.new_status in ("confirmed", "processing"):
        await _auto_create(event.order_id, event.store_id, trigger=event.new_status)


async def _auto_create(order_id: UUID, store_id: UUID, trigger: str) -> None:
    log = logger.bind(order_id=str(order_id), store_id=str(store_id), trigger=trigger)

    try:
        from src.application.services.carrier_resolver import (
            service_for_carrier,
            tracking_url_for,
        )
        from src.core.interfaces.services.shipping_service import (
            Parcel,
            ShippingAddress,
        )
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.repositories.order_repository import OrderRepository
        from src.infrastructure.repositories.shipment_repository import (
            ShipmentRepository,
        )
        from src.infrastructure.repositories.store_repository import StoreRepository

        async with AsyncSessionLocal() as session:
            store_repo = StoreRepository(session)
            order_repo = OrderRepository(session)
            shipment_repo = ShipmentRepository(session)

            store = await store_repo.get_by_id(store_id)
            if not store:
                log.warning("auto_shipment_skip", reason="store_not_found")
                return

            carrier = auto_create_carrier(store.settings)
            if carrier is None:
                return  # Silent skip - auto-create not enabled

            order = await order_repo.get_by_id(order_id)
            if not order:
                log.warning("auto_shipment_skip", reason="order_not_found")
                return

            status = getattr(order.status, "value", order.status)
            if trigger == "created" and not books_on_creation(
                store.settings, order.payment_method, status
            ):
                return

            existing = await shipment_repo.get_by_order(order.id)
            active = [
                s
                for s in existing
                if not s.is_terminal and s.shipment_type == "forward"
            ]
            if active:
                log.debug("auto_shipment_skip", reason="active_shipment_exists")
                return

            addr = order.shipping_address
            to_address = ShippingAddress(
                name=f"{addr.first_name} {addr.last_name}",
                street1=addr.address_line1,
                street2=addr.address_line2,
                city=addr.city,
                state=addr.state,
                country=addr.country or "Egypt",
                phone=addr.phone,
            )
            from_address = ShippingAddress(
                name=store.name,
                street1="Store Address",
                city="Cairo",
                country="Egypt",
            )
            parcel = Parcel(length=30, width=20, height=15, weight=1.0)

            cod_amount = 0
            if (order.payment_method or "").lower() in COD_METHODS:
                cod_amount = order.total

            shipping_service = await service_for_carrier(carrier, store.settings or {})

            try:
                label = await shipping_service.create_shipment(
                    from_address=from_address,
                    to_address=to_address,
                    parcel=parcel,
                    rate_id=f"{carrier}_standard",
                    cod_amount=cod_amount if cod_amount > 0 else None,
                    order_reference=order.order_number,
                )
            except Exception as e:
                error_msg = str(e)
                log.error(
                    "auto_shipment_create_failed", carrier=carrier, error=error_msg
                )

                # Save failed shipment record so merchant can see it and retry
                failed_shipment = Shipment(
                    store_id=store.id,
                    tenant_id=store.tenant_id,
                    order_id=order.id,
                    carrier=carrier,
                    status=ShipmentStatus.FAILED,
                    shipping_method="standard",
                    shipping_cost=order.shipping_cost,
                    cod_amount=cod_amount,
                    status_history=[
                        {
                            "from": "pending",
                            "to": "failed",
                            "description": f"Auto-create failed: {error_msg[:200]}",
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    ],
                )
                await shipment_repo.create(failed_shipment)

                order.metadata.setdefault("shipment_errors", []).append({
                    "error": error_msg,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
                await order_repo.update(order)
                await session.commit()
                return

            shipment = Shipment(
                store_id=store.id,
                tenant_id=store.tenant_id,
                order_id=order.id,
                carrier=carrier,
                carrier_shipment_id=label.carrier_shipment_id or label.tracking_number,
                tracking_number=label.tracking_number,
                tracking_url=tracking_url_for(carrier, label.tracking_number),
                awb_url=label.label_url,
                status=ShipmentStatus.CREATED,
                shipping_method="standard",
                shipping_cost=order.shipping_cost,
                cod_amount=cod_amount,
                status_history=[
                    {
                        "from": "pending",
                        "to": "created",
                        "description": f"Auto-created ({trigger})",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ],
            )
            await shipment_repo.create(shipment)

            order.tracking_number = label.tracking_number
            order.tracking_url = tracking_url_for(carrier, label.tracking_number)
            order.shipping_method = f"{carrier}_standard"
            await order_repo.update(order)

            await session.commit()

            log.info(
                "auto_shipment_created",
                carrier=carrier,
                tracking_number=label.tracking_number,
                cod_amount=cod_amount,
            )

    except Exception as e:
        log.error("auto_shipment_handler_failed", error=str(e))
