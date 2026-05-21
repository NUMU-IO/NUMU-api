"""Email notification handler for order status changes.

Sends status-appropriate emails to customers when their order
status changes. Respects customer notification preferences.
"""

from src.config.logging_config import get_logger
from src.core.events.order_events import OrderStatusChangedEvent

logger = get_logger(__name__)

# Statuses that trigger customer email notifications
_EMAIL_STATUSES = {
    "confirmed",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
}

# Maps status to the customer preference key that controls it
_PREF_KEYS = {
    "confirmed": "order_confirmation",
    "processing": "processing_update",
    "shipped": "shipping_update",
    "delivered": "delivery_confirmation",
    "cancelled": "cancellation",
    "refunded": "refund",
}


async def handle_email_notification(event: OrderStatusChangedEvent) -> None:
    """Send status-specific email to customer.

    Checks customer email preferences before sending. Each status
    maps to an email template with bilingual (en/ar) support.
    """
    if event.new_status not in _EMAIL_STATUSES:
        return

    if not event.customer_email:
        logger.info("email_notification_skip_no_email", order_id=str(event.order_id))
        return

    # Check customer preference
    pref_key = _PREF_KEYS.get(event.new_status, event.new_status)
    if not event.email_prefs.get(pref_key, True):
        logger.info(
            "email_notification_skip_disabled",
            order_id=str(event.order_id),
            status=event.new_status,
            pref_key=pref_key,
        )
        return

    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    # Routes through `send_order_status_email`, which:
    #  * maps status -> registry event_type (order_confirmed, etc.),
    #  * applies merchant custom-template overrides when the renderer is
    #    wired (FastAPI request path) — handlers run from the global
    #    event bus and currently get a worker-style service without a
    #    renderer, so they fall through to the legacy body. Passing
    #    `store_id` here is forward-compatible: once the bus runs with
    #    a renderer the same call-site automatically picks up overrides.
    service = ResendEmailService()
    # NUMU emails are Egyptian Arabic only by default; respect the event
    # language if the order/customer overrides it.
    language = event.language or "ar"
    try:
        result = await service.send_order_status_email(
            email=event.customer_email,
            status=event.new_status,
            order_number=event.order_number,
            store_name=event.store_name,
            customer_name=event.customer_name,
            tracking_number=event.tracking_number,
            carrier=event.carrier,
            reason=event.reason,
            language=language,
            store_id=event.store_id,
        )
    except Exception:
        logger.exception(
            "order_status_email_failed",
            order_id=str(event.order_id),
            status=event.new_status,
        )
        return

    if not result:
        logger.warning(
            "email_notification_no_template",
            status=event.new_status,
            order_id=str(event.order_id),
        )
        return

    logger.info(
        "order_status_email_sent",
        order_id=str(event.order_id),
        status=event.new_status,
        email=event.customer_email,
        success=result,
    )
