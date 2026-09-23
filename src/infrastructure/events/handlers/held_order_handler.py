"""Announce a held card-gateway order once its payment lands.

Storefront checkout parks card-gateway orders in AWAITING_PAYMENT and skips
OrderCreatedEvent, so the merchant sees nothing (no email, push, feed,
WhatsApp confirmation, webhook) for an order the customer may never pay.
Every gateway webhook publishes OrderStatusChangedEvent with the old status
when it marks the order paid; this handler turns that transition into the
OrderCreatedEvent checkout held back.
"""

from src.core.entities.order import OrderStatus
from src.core.events.order_events import OrderCreatedEvent, OrderStatusChangedEvent
from src.core.logging import get_logger

logger = get_logger(__name__)


async def handle_held_order_paid(event: OrderStatusChangedEvent) -> None:
    if event.previous_status != OrderStatus.AWAITING_PAYMENT.value:
        return
    if event.new_status != OrderStatus.PROCESSING.value:
        return

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.events.setup import get_event_bus
    from src.infrastructure.repositories.order_repository import OrderRepository

    async with AsyncSessionLocal() as session:
        order = await OrderRepository(session).get_by_id(event.order_id)
    if order is None:
        logger.warning("held_order_paid_order_missing", order_id=str(event.order_id))
        return

    get_event_bus().publish(
        OrderCreatedEvent(
            order_id=order.id,
            order_number=order.order_number,
            store_id=order.store_id,
            customer_id=order.customer_id,
            total=float(order.total),
            currency=order.currency,
        )
    )
    logger.info("held_order_released", order_id=str(order.id))
