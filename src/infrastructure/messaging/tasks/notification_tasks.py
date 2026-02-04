"""Celery tasks for async notification dispatch.

Provides fire-and-forget tasks for sending order notifications
via WhatsApp and email without blocking the order flow.
"""

import asyncio

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)


def run_async(coro):
    """Run async code in Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Email tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.send_order_confirmation_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_order_confirmation_email_task(
    self,
    email: str,
    order_number: str,
    order_details: dict,
    language: str = "en",
):
    """Send order confirmation email asynchronously.

    Args:
        email: Customer email address.
        order_number: Order reference number.
        order_details: Dict with items list and total.
    """
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    try:
        service = ResendEmailService()
        result = run_async(
            service.send_order_confirmation(email, order_number, order_details, language=language)
        )
        logger.info(
            "order_confirmation_email_sent",
            email=email,
            order_number=order_number,
            success=result,
        )
        return {"sent": result, "order_number": order_number}
    except Exception as e:
        logger.error(
            "order_confirmation_email_failed",
            email=email,
            order_number=order_number,
            error=str(e),
        )
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_shipping_notification_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_shipping_notification_email_task(
    self,
    email: str,
    order_number: str,
    tracking_number: str | None = None,
    carrier: str | None = None,
    language: str = "en",
):
    """Send shipping notification email asynchronously.

    Args:
        email: Customer email address.
        order_number: Order reference number.
        tracking_number: Optional tracking number.
        carrier: Optional carrier name.
    """
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    try:
        service = ResendEmailService()
        result = run_async(
            service.send_shipping_notification(
                email, order_number, tracking_number, carrier, language=language
            )
        )
        logger.info(
            "shipping_notification_email_sent",
            email=email,
            order_number=order_number,
            success=result,
        )
        return {"sent": result, "order_number": order_number}
    except Exception as e:
        logger.error(
            "shipping_notification_email_failed",
            email=email,
            order_number=order_number,
            error=str(e),
        )
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_delivery_confirmation_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_delivery_confirmation_email_task(
    self,
    email: str,
    order_number: str,
    store_name: str,
    language: str = "en",
):
    """Send delivery confirmation email asynchronously.

    Args:
        email: Customer email address.
        order_number: Order reference number.
        store_name: Name of the store.
    """
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.notifications import (
        DELIVERY_CONFIRMATION_TEMPLATE,
    )

    try:
        service = ResendEmailService()
        html_content = DELIVERY_CONFIRMATION_TEMPLATE["html_fn"](
            order_number=order_number,
            store_name=store_name,
            language=language,
        )
        subject = DELIVERY_CONFIRMATION_TEMPLATE["subject_fn"](order_number, store_name, language=language)
        message = EmailMessage(
            to=email,
            subject=subject,
            html_content=html_content,
        )
        result = run_async(service.send_email(message))
        logger.info(
            "delivery_confirmation_email_sent",
            email=email,
            order_number=order_number,
            success=result,
        )
        return {"sent": result, "order_number": order_number}
    except Exception as e:
        logger.error(
            "delivery_confirmation_email_failed",
            email=email,
            order_number=order_number,
            error=str(e),
        )
        raise self.retry(exc=e)


# ---------------------------------------------------------------------------
# WhatsApp tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.send_whatsapp_order_confirmation",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_whatsapp_order_confirmation_task(
    self,
    phone: str,
    customer_name: str,
    order_number: str,
    total: str,
    store_name: str,
    language: str = "en",
):
    """Send order confirmation via WhatsApp asynchronously.

    Args:
        phone: Customer phone number.
        customer_name: Customer display name.
        order_number: Order reference number.
        total: Formatted total amount (e.g. "EGP 250.00").
        store_name: Store name.
        language: Preferred language code.
    """
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        WhatsAppMessagingService,
    )

    try:
        service = WhatsAppMessagingService()
        recipient = MessageRecipient(
            phone=phone, name=customer_name, language=language
        )
        result = run_async(
            service.send_order_confirmation(recipient, order_number, total, store_name)
        )
        logger.info(
            "whatsapp_order_confirmation_sent",
            phone=phone,
            order_number=order_number,
            success=result.success,
        )
        return {
            "sent": result.success,
            "message_id": result.message_id,
            "order_number": order_number,
        }
    except Exception as e:
        logger.error(
            "whatsapp_order_confirmation_failed",
            phone=phone,
            order_number=order_number,
            error=str(e),
        )
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_whatsapp_shipping_update",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_whatsapp_shipping_update_task(
    self,
    phone: str,
    customer_name: str,
    order_number: str,
    tracking_number: str,
    carrier: str = "Bosta",
    language: str = "en",
):
    """Send shipping update via WhatsApp asynchronously.

    Args:
        phone: Customer phone number.
        customer_name: Customer display name.
        order_number: Order reference number.
        tracking_number: Carrier tracking number.
        carrier: Shipping carrier name.
        language: Preferred language code.
    """
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        WhatsAppMessagingService,
    )

    try:
        service = WhatsAppMessagingService()
        recipient = MessageRecipient(
            phone=phone, name=customer_name, language=language
        )
        result = run_async(
            service.send_shipping_notification(
                recipient, order_number, tracking_number, carrier
            )
        )
        logger.info(
            "whatsapp_shipping_update_sent",
            phone=phone,
            order_number=order_number,
            success=result.success,
        )
        return {
            "sent": result.success,
            "message_id": result.message_id,
            "order_number": order_number,
        }
    except Exception as e:
        logger.error(
            "whatsapp_shipping_update_failed",
            phone=phone,
            order_number=order_number,
            error=str(e),
        )
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_whatsapp_delivery_confirmation",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_whatsapp_delivery_confirmation_task(
    self,
    phone: str,
    customer_name: str,
    order_number: str,
    store_name: str,
    language: str = "en",
):
    """Send delivery confirmation via WhatsApp asynchronously.

    Args:
        phone: Customer phone number.
        customer_name: Customer display name.
        order_number: Order reference number.
        store_name: Store name.
        language: Preferred language code.
    """
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        WhatsAppMessagingService,
    )

    try:
        service = WhatsAppMessagingService()
        recipient = MessageRecipient(
            phone=phone, name=customer_name, language=language
        )
        result = run_async(
            service.send_delivery_notification(recipient, order_number, store_name)
        )
        logger.info(
            "whatsapp_delivery_confirmation_sent",
            phone=phone,
            order_number=order_number,
            success=result.success,
        )
        return {
            "sent": result.success,
            "message_id": result.message_id,
            "order_number": order_number,
        }
    except Exception as e:
        logger.error(
            "whatsapp_delivery_confirmation_failed",
            phone=phone,
            order_number=order_number,
            error=str(e),
        )
        raise self.retry(exc=e)
