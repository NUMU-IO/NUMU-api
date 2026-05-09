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
    store_id: str | None = None,
    tenant_id: str | None = None,
):
    """Send order confirmation email asynchronously.

    Args:
        email: Customer email address.
        order_number: Order reference number.
        order_details: Dict with items list and total.
        store_id: Owning store id (UUID string). Used to pick up the
            merchant custom template, if any, and write an audit log.
        tenant_id: Owning tenant id (UUID string).
    """
    from uuid import UUID

    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    # TODO(email-templates): wire renderer + email_log_repo into Celery
    # worker — needs a per-task DB session. For now, the worker uses the
    # legacy path (no merchant overrides, no audit log row). The FastAPI
    # request path (where ResendEmailService is built via DI in
    # api/dependencies/services.py) DOES use the renderer.
    try:
        service = ResendEmailService()
        store_uuid = UUID(store_id) if store_id else None
        tenant_uuid = UUID(tenant_id) if tenant_id else None
        result = run_async(
            service.send_order_confirmation(
                email,
                order_number,
                order_details,
                language=language,
                store_id=store_uuid,
                tenant_id=tenant_uuid,
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
    store_id: str | None = None,
    tenant_id: str | None = None,
    store_name: str = "NUMU",
    customer_name: str | None = None,
):
    """Send shipping notification email asynchronously.

    Args:
        email: Customer email address.
        order_number: Order reference number.
        tracking_number: Optional tracking number.
        carrier: Optional carrier name.
        store_id: Owning store id (UUID string).
        tenant_id: Owning tenant id (UUID string).
        store_name: Store display name (for the body copy).
        customer_name: Customer display name (for the greeting).
    """
    from uuid import UUID

    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    # TODO(email-templates): wire renderer into Celery worker — needs
    # per-task DB session. Legacy path until then.
    try:
        service = ResendEmailService()
        store_uuid = UUID(store_id) if store_id else None
        tenant_uuid = UUID(tenant_id) if tenant_id else None
        result = run_async(
            service.send_shipping_notification(
                email,
                order_number,
                tracking_number,
                carrier,
                language=language,
                store_id=store_uuid,
                tenant_id=tenant_uuid,
                store_name=store_name,
                customer_name=customer_name,
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
    store_id: str | None = None,
    tenant_id: str | None = None,
    customer_name: str | None = None,
):
    """Send delivery confirmation email asynchronously.

    Args:
        email: Customer email address.
        order_number: Order reference number.
        store_name: Name of the store.
        store_id: Owning store id (UUID string).
        tenant_id: Owning tenant id (UUID string).
        customer_name: Customer display name (for the greeting).
    """
    from uuid import UUID

    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    # TODO(email-templates): wire renderer into Celery worker — needs
    # per-task DB session. Legacy path until then. Routing through the
    # service-level helper means once the worker IS wired, this task
    # automatically picks up merchant overrides without code changes.
    try:
        service = ResendEmailService()
        store_uuid = UUID(store_id) if store_id else None
        tenant_uuid = UUID(tenant_id) if tenant_id else None
        result = run_async(
            service.send_order_status_email(
                email=email,
                status="delivered",
                order_number=order_number,
                store_name=store_name,
                customer_name=customer_name,
                language=language,
                store_id=store_uuid,
                tenant_id=tenant_uuid,
            )
        )
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
    tracking_url: str | None = None,
):
    """Send order confirmation via WhatsApp asynchronously.

    Args:
        phone: Customer phone number.
        customer_name: Customer display name.
        order_number: Order reference number.
        total: Formatted total amount (e.g. "EGP 250.00").
        store_name: Store name.
        language: Preferred language code.
        tracking_url: Persistent tracking URL to embed in the message.
    """
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        WhatsAppMessagingService,
    )

    try:
        service = WhatsAppMessagingService()
        recipient = MessageRecipient(phone=phone, name=customer_name, language=language)
        result = run_async(
            service.send_order_confirmation(
                recipient,
                order_number,
                total,
                store_name,
                tracking_url=tracking_url,
            )
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


# ---------------------------------------------------------------------------
# Back-in-stock notification (Phase 3.5)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.send_back_in_stock_email",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_back_in_stock_email_task(
    self,
    store_id: str,
    product_id: str,
    email: str,
    variant_id: str | None = None,
):
    """Notify a subscriber that a product is back in stock.

    v1 stub: logs the dispatch + returns. Phase 5 wires the actual
    Resend template (a "back-in-stock" email with the product name,
    image, and CTA back to the PDP). The sweep task stamps
    `notified_at` on the subscription row regardless, so this is
    idempotent — re-runs of the sweep won't re-fire the same email.

    Args:
        store_id: Owning store id (UUID string).
        product_id: Product that came back in stock (UUID string).
        email: Recipient email.
        variant_id: Optional variant the customer was waiting on.
    """
    logger.info(
        "back_in_stock_email_queued",
        email=email,
        store_id=store_id,
        product_id=product_id,
        variant_id=variant_id,
        # Phase 5: replace this log+return with a real template send via
        # ResendEmailService.send_back_in_stock(). Until then the
        # subscription is marked notified by the sweep but no actual
        # email goes out — surfacing an opt-in feature without a working
        # delivery path is a partial-implementation footgun (per CLAUDE
        # guidance), so we explicitly note this here.
        note="Phase 5: wire ResendEmailService.send_back_in_stock template",
    )
    return {"queued": True, "email": email, "product_id": product_id}
