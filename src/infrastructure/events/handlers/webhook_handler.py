"""Outgoing webhook handler.

Bridges the internal event bus to the outgoing webhook delivery system.
Each handler picks the relevant fields from the domain event and calls
WebhookDeliveryService.dispatch(), which fans out to all active subscriptions.
"""

from src.config.logging_config import get_logger
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
