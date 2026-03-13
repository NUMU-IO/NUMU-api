"""WhatsApp notification handler for order status changes."""

from src.config.logging_config import get_logger
from src.core.events.order_events import OrderStatusChangedEvent

logger = get_logger(__name__)

_WHATSAPP_STATUSES = {"shipped", "delivered"}

_WA_PREF_KEYS = {
    "shipped": "shipping_update",
    "delivered": "delivery_confirmation",
}


async def handle_whatsapp_notification(event: OrderStatusChangedEvent) -> None:
    """Send WhatsApp notification for shipped/delivered status changes."""
    if event.new_status not in _WHATSAPP_STATUSES:
        return

    if not event.customer_phone:
        return

    pref_key = _WA_PREF_KEYS.get(event.new_status, event.new_status)
    if not event.whatsapp_prefs.get(pref_key, True):
        return

    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        WhatsAppMessagingService,
    )

    service = WhatsAppMessagingService()
    recipient = MessageRecipient(
        phone=event.customer_phone,
        name=event.customer_name or "",
        language=event.language,
    )

    if event.new_status == "shipped":
        result = await service.send_shipping_notification(
            recipient,
            event.order_number,
            event.tracking_number or "N/A",
            event.carrier or "Bosta",
        )
    elif event.new_status == "delivered":
        result = await service.send_delivery_notification(
            recipient,
            event.order_number,
            event.store_name,
        )
    else:
        return

    logger.info(
        "whatsapp_order_notification_sent",
        order_id=str(event.order_id),
        status=event.new_status,
        phone=event.customer_phone,
        success=result.success,
    )
