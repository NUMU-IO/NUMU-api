"""Outgoing webhook handler.

Bridges the internal event bus to the outgoing webhook delivery system.
Each handler picks the relevant fields from the domain event and calls
WebhookDeliveryService.dispatch(), which fans out to all active subscriptions.
"""

from src.core.events.commerce_events import (
    CheckoutAbandonedEvent,
    CustomerCreatedEvent,
    CustomerUpdatedEvent,
    InventoryLevelChangedEvent,
    RefundCompletedEvent,
    RefundCreatedEvent,
    ShipmentCreatedEvent,
    ShipmentStatusChangedEvent,
)
from src.core.events.order_events import (
    OrderCreatedEvent,
    OrderPaidEvent,
    OrderStatusChangedEvent,
)
from src.core.events.product_events import (
    ProductCreatedEvent,
    ProductDeletedEvent,
    ProductUpdatedEvent,
)
from src.core.logging import get_logger

logger = get_logger(__name__)


async def _dispatch(store_id, event_type, event_id, data: dict) -> None:
    from src.application.services.webhook_delivery_service import WebhookDeliveryService
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.webhook_delivery_log_repository import (
        WebhookDeliveryLogRepository,
    )
    from src.infrastructure.repositories.webhook_subscription_repository import (
        WebhookSubscriptionRepository,
    )

    async with AsyncSessionLocal() as session:
        sub_repo = WebhookSubscriptionRepository(session)
        log_repo = WebhookDeliveryLogRepository(session)
        service = WebhookDeliveryService(sub_repo, log_repo)
        await service.dispatch(store_id, event_type, event_id, data)
        await session.commit()


async def handle_webhook_order_created(event: OrderCreatedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id,
        WebhookEventType.ORDER_CREATED,
        event.event_id,
        {
            "order_id": str(event.order_id),
            "order_number": event.order_number,
            "customer_id": str(event.customer_id),
            "total": event.total,
            "currency": event.currency,
        },
    )


async def handle_webhook_order_paid(event: OrderPaidEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id,
        WebhookEventType.ORDER_PAID,
        event.event_id,
        {
            "order_id": str(event.order_id),
            "order_number": event.order_number,
            "payment_id": event.payment_id,
            "payment_method": event.payment_method,
            "total": event.total,
        },
    )


async def handle_webhook_order_status_changed(event: OrderStatusChangedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id,
        WebhookEventType.ORDER_STATUS_CHANGED,
        event.event_id,
        {
            "order_id": str(event.order_id),
            "order_number": event.order_number,
            "previous_status": event.previous_status,
            "new_status": event.new_status,
            "reason": event.reason,
            "tracking_number": event.tracking_number,
        },
    )


async def handle_webhook_product_created(event: ProductCreatedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id,
        WebhookEventType.PRODUCT_CREATED,
        event.event_id,
        {
            "product_id": str(event.product_id),
            "name": event.name,
            "sku": event.sku,
        },
    )


async def handle_webhook_product_updated(event: ProductUpdatedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id,
        WebhookEventType.PRODUCT_UPDATED,
        event.event_id,
        {
            "product_id": str(event.product_id),
            "name": event.name,
        },
    )


async def handle_webhook_product_deleted(event: ProductDeletedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id,
        WebhookEventType.PRODUCT_DELETED,
        event.event_id,
        {
            "product_id": str(event.product_id),
        },
    )


#: Stock moves in bursts: a sale debits every line of every order. The first
#: change of a product or variant opens this window; later changes in it are
#: absorbed, and one webhook carries the level when the window closes.
INVENTORY_WINDOW_SECONDS = 30

_EVENT_FIELDS = {"event_id", "timestamp", "event_type", "store_id"}


def _data(event) -> dict:
    return event.model_dump(mode="json", exclude=_EVENT_FIELDS)


async def handle_webhook_customer_created(event: CustomerCreatedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id, WebhookEventType.CUSTOMER_CREATED, event.event_id, _data(event)
    )


async def handle_webhook_customer_updated(event: CustomerUpdatedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id, WebhookEventType.CUSTOMER_UPDATED, event.event_id, _data(event)
    )


async def handle_webhook_refund_created(event: RefundCreatedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id, WebhookEventType.REFUND_CREATED, event.event_id, _data(event)
    )


async def handle_webhook_refund_completed(event: RefundCompletedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id, WebhookEventType.REFUND_COMPLETED, event.event_id, _data(event)
    )


async def handle_webhook_shipment_created(event: ShipmentCreatedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id, WebhookEventType.SHIPMENT_CREATED, event.event_id, _data(event)
    )


async def handle_webhook_shipment_status_changed(
    event: ShipmentStatusChangedEvent,
) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id,
        WebhookEventType.SHIPMENT_STATUS_CHANGED,
        event.event_id,
        _data(event),
    )


async def handle_webhook_checkout_abandoned(event: CheckoutAbandonedEvent) -> None:
    from src.core.entities.webhook import WebhookEventType

    await _dispatch(
        event.store_id,
        WebhookEventType.CHECKOUT_ABANDONED,
        event.event_id,
        _data(event),
    )


async def handle_webhook_inventory_level_changed(
    event: InventoryLevelChangedEvent,
) -> None:
    """Opens the store's window for this product or variant, when anyone
    listens. With no Redis the level is sent at once."""
    from src.config import settings
    from src.core.entities.webhook import WebhookEventType
    from src.infrastructure.cache.redis_cache import RedisCacheService
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.messaging.tasks.webhook_tasks import (
        send_inventory_level_task,
    )
    from src.infrastructure.repositories.webhook_subscription_repository import (
        WebhookSubscriptionRepository,
    )

    async with AsyncSessionLocal() as session:
        listening = await WebhookSubscriptionRepository(session).get_active_for_event(
            event.store_id, WebhookEventType.INVENTORY_LEVEL_CHANGED
        )
    if not listening:
        return
    args = [str(event.store_id), str(event.product_id), str(event.variant_id or "")]
    if not settings.redis_host:
        await send_inventory_level(*args)
        return
    cache = RedisCacheService()
    try:
        opened = await cache.set_if_absent(
            inventory_window_key(*args), "1", expire=INVENTORY_WINDOW_SECONDS * 4
        )
    finally:
        await cache.close()
    if opened:
        send_inventory_level_task.apply_async(
            args=args, countdown=INVENTORY_WINDOW_SECONDS
        )


def inventory_window_key(store_id: str, product_id: str, variant_id: str) -> str:
    return f"webhook:inventory:{store_id}:{product_id}:{variant_id}"


async def send_inventory_level(store_id: str, product_id: str, variant_id: str) -> None:
    """Close the window and send the level as it is now."""
    from uuid import UUID, uuid4

    from src.config import settings
    from src.core.entities.webhook import WebhookEventType
    from src.infrastructure.cache.redis_cache import RedisCacheService
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.product import ProductModel
    from src.infrastructure.database.models.tenant.variant import VariantModel
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    if settings.redis_host:
        cache = RedisCacheService()
        try:
            await cache.delete(inventory_window_key(store_id, product_id, variant_id))
        finally:
            await cache.close()
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        row = await session.get(
            VariantModel if variant_id else ProductModel,
            UUID(variant_id or product_id),
        )
        if row is None:
            return
        quantity = row.inventory_quantity if variant_id else row.quantity
    await _dispatch(
        UUID(store_id),
        WebhookEventType.INVENTORY_LEVEL_CHANGED,
        uuid4(),
        {
            "product_id": product_id,
            "variant_id": variant_id or None,
            "quantity": quantity,
        },
    )
