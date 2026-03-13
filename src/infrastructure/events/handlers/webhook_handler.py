"""Webhook handler for order status changes.

Fires merchant-configured webhook URLs when order statuses change,
enabling external integrations (shipping providers, ERP, analytics).
"""

import httpx

from src.config.logging_config import get_logger
from src.core.events.order_events import OrderStatusChangedEvent

logger = get_logger(__name__)

# Timeout for webhook delivery
_WEBHOOK_TIMEOUT = 10.0


async def handle_webhook(event: OrderStatusChangedEvent) -> None:
    """Deliver order status change payload to merchant webhook URLs.

    Reads webhook configuration from the store's settings. If no webhook
    is configured, this handler is a no-op.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.store_repository import StoreRepository

    async with AsyncSessionLocal() as session:
        repo = StoreRepository(session)
        store = await repo.get_by_id(event.store_id)

    if not store:
        return

    # Webhook URL is stored in store.settings
    webhook_url = None
    if store.settings:
        webhook_url = store.settings.get("webhook_url") or store.settings.get(
            "webhooks", {}
        ).get("order_status")

    if not webhook_url:
        return

    payload = {
        "event": "order.status_changed",
        "order_id": str(event.order_id),
        "order_number": event.order_number,
        "previous_status": event.previous_status,
        "new_status": event.new_status,
        "timestamp": event.timestamp.isoformat(),
        "tracking_number": event.tracking_number,
        "reason": event.reason,
    }

    try:
        async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        logger.info(
            "webhook_delivered",
            order_id=str(event.order_id),
            url=webhook_url,
            status_code=response.status_code,
        )
    except Exception as exc:
        logger.warning(
            "webhook_delivery_failed",
            order_id=str(event.order_id),
            url=webhook_url,
            error=str(exc),
        )
