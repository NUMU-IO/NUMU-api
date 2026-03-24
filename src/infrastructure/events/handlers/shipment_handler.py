"""Auto-create Bosta shipment when order status changes to confirmed/processing.

Subscribes to OrderStatusChangedEvent and creates a shipment automatically
if the store has Bosta configured with auto_create_shipment enabled.
"""

from datetime import UTC, datetime

from src.config.logging_config import get_logger
from src.core.entities.shipment import Shipment, ShipmentStatus
from src.core.events.order_events import OrderStatusChangedEvent

logger = get_logger(__name__)


async def handle_order_status_for_shipment(event: OrderStatusChangedEvent) -> None:
    """Auto-create a Bosta shipment when order is confirmed or moves to processing.

    Only triggers if:
    - new_status is 'confirmed' or 'processing'
    - Store has Bosta enabled with auto_create_shipment
    - No active forward shipment already exists for the order
    """
    if event.new_status not in ("confirmed", "processing"):
        return

    log = logger.bind(
        order_id=str(event.order_id),
        store_id=str(event.store_id),
        new_status=event.new_status,
    )

    try:
        from src.core.interfaces.services.shipping_service import (
            Parcel,
            ShippingAddress,
        )
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.external_services.bosta.shipping_service import (
            get_bosta_service_for_store,
        )
        from src.infrastructure.repositories.order_repository import OrderRepository
        from src.infrastructure.repositories.shipment_repository import (
            ShipmentRepository,
        )
        from src.infrastructure.repositories.store_repository import StoreRepository

        async with AsyncSessionLocal() as session:
            store_repo = StoreRepository(session)
            order_repo = OrderRepository(session)
            shipment_repo = ShipmentRepository(session)

            store = await store_repo.get_by_id(event.store_id)
            if not store:
                log.warning("auto_shipment_skip", reason="store_not_found")
                return

            # Check Bosta is enabled with auto-create
            shipping_settings = (store.settings or {}).get("shipping", {})
            bosta_settings = shipping_settings.get("bosta", {})
            if not bosta_settings.get("enabled") or not bosta_settings.get(
                "auto_create_shipment"
            ):
                return  # Silent skip - auto-create not enabled

            order = await order_repo.get_by_id(event.order_id)
            if not order:
                log.warning("auto_shipment_skip", reason="order_not_found")
                return

            # Idempotency: check no active shipment exists
            existing = await shipment_repo.get_by_order(order.id)
            active = [
                s
                for s in existing
                if not s.is_terminal and s.shipment_type == "forward"
            ]
            if active:
                log.debug("auto_shipment_skip", reason="active_shipment_exists")
                return

            # Build addresses
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

            # COD
            cod_amount = 0
            if order.payment_method and order.payment_method.lower() in (
                "cod",
                "cash_on_delivery",
            ):
                cod_amount = order.total

            bosta_service = await get_bosta_service_for_store(store.settings or {})

            try:
                label = await bosta_service.create_shipment(
                    from_address=from_address,
                    to_address=to_address,
                    parcel=parcel,
                    rate_id="bosta_standard",
                    cod_amount=cod_amount if cod_amount > 0 else None,
                    order_reference=order.order_number,
                )
            except Exception as e:
                log.error("auto_shipment_bosta_failed", error=str(e))
                # Record failure in order metadata but don't block
                order.metadata.setdefault("shipment_errors", []).append({
                    "error": str(e),
                    "timestamp": datetime.now(UTC).isoformat(),
                })
                await order_repo.update(order)
                await session.commit()
                return

            # Create shipment record
            shipment = Shipment(
                store_id=store.id,
                tenant_id=store.tenant_id,
                order_id=order.id,
                carrier="bosta",
                carrier_shipment_id=label.tracking_number,
                tracking_number=label.tracking_number,
                tracking_url=f"https://bosta.co/tracking-shipment/?tracking_number={label.tracking_number}",
                awb_url=label.label_url,
                status=ShipmentStatus.CREATED,
                shipping_method="standard",
                shipping_cost=order.shipping_cost,
                cod_amount=cod_amount,
                status_history=[
                    {
                        "from": "pending",
                        "to": "created",
                        "description": "Auto-created on order confirmation",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ],
            )
            await shipment_repo.create(shipment)

            # Update order with tracking info
            order.tracking_number = label.tracking_number
            order.tracking_url = f"https://bosta.co/tracking-shipment/?tracking_number={label.tracking_number}"
            order.shipping_method = "bosta_standard"
            await order_repo.update(order)

            await session.commit()

            log.info(
                "auto_shipment_created",
                tracking_number=label.tracking_number,
                cod_amount=cod_amount,
            )

    except Exception as e:
        log.error("auto_shipment_handler_failed", error=str(e))
