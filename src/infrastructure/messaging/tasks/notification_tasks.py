"""Celery tasks for async notification dispatch.

Provides fire-and-forget tasks for sending order notifications
via WhatsApp and email without blocking the order flow.
"""

import asyncio

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro):
    """Run async code in Celery task.

    Reuses a persistent event loop per worker thread so that cached
    async connections (e.g. Redis) aren't invalidated between tasks.
    """
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


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
    language: str = "ar",
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
            service.send_order_confirmation(
                email, order_number, order_details, language=language
            )
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
    language: str = "ar",
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
    language: str = "ar",
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
        subject = DELIVERY_CONFIRMATION_TEMPLATE["subject_fn"](
            order_number, store_name, language=language
        )
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
# Invoice tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.generate_and_send_invoice",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def generate_and_send_invoice_task(
    self,
    store_id: str,
    order_id: str,
    order_number: str,
    customer_id: str,
    customer_email: str,
    customer_name: str,
    line_items: list[dict],
    subtotal: int,
    tax_amount: int,
    discount_amount: int,
    total: int,
    currency: str,
    store_name: str,
    shipping_address: dict | None = None,
    language: str = "ar",
):
    """Generate invoice from order data, create PDF, persist to DB, and email customer.

    This task runs asynchronously after checkout to:
    1. Create an Invoice entity from order data
    2. Generate a bilingual PDF using the ETA-compliant template
    3. Persist the invoice to the database
    4. Email the PDF to the customer
    """
    from decimal import Decimal
    from uuid import UUID, uuid4

    from src.core.entities.invoice import (
        BuyerInfo,
        Invoice,
        InvoiceStatus,
        SellerInfo,
    )

    try:
        # Build seller info from store
        seller = SellerInfo(
            tax_id="",  # Will be populated from store settings if available
            name=store_name,
            name_ar=store_name,
        )

        # Build buyer info from customer/shipping address
        buyer_name = customer_name or "Customer"
        buyer_city = ""
        buyer_street = ""
        buyer_phone = ""
        if shipping_address:
            buyer_name = (
                f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}".strip()
                or buyer_name
            )
            buyer_city = shipping_address.get("city", "")
            buyer_street = shipping_address.get("address_line1", "")
            buyer_phone = shipping_address.get("phone", "")

        buyer = BuyerInfo(
            buyer_type="P",  # Person (consumer)
            name=buyer_name,
            name_ar=buyer_name,
            city=buyer_city,
            street=buyer_street,
            phone=buyer_phone,
            email=customer_email,
        )

        # Generate invoice number via DB
        async def _create_invoice():
            from src.infrastructure.database.connection import AsyncSessionLocal
            from src.infrastructure.repositories.invoice_repository import (
                InvoiceRepository,
            )

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = InvoiceRepository(session)
                    inv_number = await repo.get_next_invoice_number(UUID(store_id))

                    # Create invoice entity
                    invoice = Invoice(
                        id=uuid4(),
                        store_id=UUID(store_id),
                        order_id=UUID(order_id),
                        customer_id=UUID(customer_id),
                        invoice_number=inv_number,
                        internal_id=order_number,
                        status=InvoiceStatus.ACCEPTED,
                        seller=seller,
                        buyer=buyer,
                        currency=currency,
                    )

                    # Add line items
                    for item in line_items:
                        unit_price_decimal = (
                            Decimal(str(item["price"]))
                            if item.get("price")
                            else Decimal(str(item.get("unit_price", 0))) / 100
                        )
                        invoice.add_line_item(
                            description=item.get(
                                "name", item.get("product_name", "Item")
                            ),
                            description_ar=item.get(
                                "name", item.get("product_name", "منتج")
                            ),
                            item_code=item.get("sku", "EG-0000-0000"),
                            quantity=Decimal(str(item.get("quantity", 1))),
                            unit_price=unit_price_decimal,
                            internal_code=item.get("sku"),
                        )

                    # Persist to DB
                    created_invoice = await repo.create(invoice)
                    return created_invoice

        invoice = run_async(_create_invoice())

        # Generate PDF
        from src.infrastructure.external_services.invoice import InvoicePDFGenerator

        generator = InvoicePDFGenerator(
            template_name="invoice_ar.html",
            language="ar_en",
        )
        pdf_bytes = generator.generate(invoice)

        # Upload PDF to R2 (best-effort)
        async def _upload_pdf():
            try:
                from src.core.interfaces.services.storage_service import StorageBucket
                from src.infrastructure.external_services.cloudflare_r2 import (
                    CloudflareR2StorageService,
                )

                r2 = CloudflareR2StorageService()
                if r2.client:
                    uploaded = await r2.upload_file(
                        file_content=pdf_bytes,
                        filename=f"{invoice.invoice_number}.pdf",
                        content_type="application/pdf",
                        bucket=StorageBucket.DOCUMENTS,
                    )
                    # Update invoice with PDF URL
                    from src.infrastructure.database.connection import (
                        AsyncSessionLocal,
                    )
                    from src.infrastructure.repositories.invoice_repository import (
                        InvoiceRepository,
                    )

                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            repo = InvoiceRepository(session)
                            invoice.pdf_r2_key = uploaded.key
                            invoice.pdf_url = uploaded.url
                            await repo.update(invoice)
            except Exception:
                logger.warning(
                    "invoice_pdf_r2_upload_failed", invoice_id=str(invoice.id)
                )

        run_async(_upload_pdf())

        # Send email with PDF attachment
        from src.infrastructure.external_services.resend.email_service import (
            ResendEmailService,
        )

        service = ResendEmailService()
        result = run_async(
            service.send_invoice_email(
                email=customer_email,
                order_number=order_number,
                invoice_number=invoice.invoice_number,
                pdf_bytes=pdf_bytes,
                store_name=store_name,
                language=language,
            )
        )
        logger.info(
            "invoice_generated_and_sent",
            invoice_number=invoice.invoice_number,
            order_number=order_number,
            email=customer_email,
            success=result,
        )
        return {
            "sent": result,
            "invoice_number": invoice.invoice_number,
            "order_number": order_number,
        }
    except Exception as e:
        logger.error(
            "invoice_generation_failed",
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
    language: str = "ar",
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
        recipient = MessageRecipient(phone=phone, name=customer_name, language=language)
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
    language: str = "ar",
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
        recipient = MessageRecipient(phone=phone, name=customer_name, language=language)
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
    language: str = "ar",
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
        recipient = MessageRecipient(phone=phone, name=customer_name, language=language)
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


# ---------------------------------------------------------------------------
# Generic staff notification task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.send_email",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_email_task(
    self,
    to: str,
    subject: str,
    template: str,
    context: dict | None = None,
):
    """Send generic email via template.

    Args:
        to: Recipient email address
        subject: Email subject line
        template: Template name (staff_invitation, access_request_created, etc.)
        context: Template variables dict
    """
    from src.infrastructure.external_services.resend.email_service import (
        EmailMessage,
        ResendEmailService,
    )

    service = ResendEmailService()

    template_map = {
        "staff_invitation": "dJXzpO4E0001",
        "access_request_created": "dJXzpO4E0002",
        "access_request_approved": "dJXzpO4E0003",
        "access_request_denied": "dJXzpO4E0004",
        "temporary_access_granted": "dJXzpO4E0005",
        "staff_activated": "dJXzpO4E0006",
        "password_reset": "dJXzpO4E0007",
        "welcome": "dJXzpO4E0008",
    }

    template_id = template_map.get(template)

    if not template_id:
        logger.warning("unknown_email_template", template=template)
        template_id = "dJXzpO4E0008"  # Default to welcome

    message = EmailMessage(
        to=to,
        subject=subject,
        template_id=template_id,
        context=context or {},
    )

    try:
        result = run_async(service.send_email(message))
        logger.info(
            "email_sent",
            to=to,
            template=template,
            success=result,
        )
        return {"sent": result, "to": to, "template": template}
    except Exception as e:
        logger.error(
            "email_send_failed",
            to=to,
            template=template,
            error=str(e),
        )
        raise self.retry(exc=e)
