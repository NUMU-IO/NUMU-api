"""Generate an invoice when an order's payment is confirmed.

This handler issues the invoice automatically on `OrderPaidEvent`, fired by:
  * the merchant-hub "Mark as paid" action (/orders/:id/mark-paid), and
  * any payment-gateway capture webhook.

A merchant can also issue it earlier, from the order screen, to pack with a
cash-on-delivery parcel. That invoice is found here by order id and reused —
its PDF reads payment status live from the order, so it now shows as paid.

The handler is idempotent — if an invoice already exists for the order it
short-circuits. That way re-firing the event (e.g. webhook retry) doesn't
produce duplicate invoices.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from src.application.services.invoice_from_order import (
    apply_tax_authority_step,
    invoice_from_order,
    lock_order_invoicing,
)
from src.core.entities.invoice import (
    Invoice,
)
from src.core.events.order_events import OrderPaidEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.invoice import InvoiceModel
from src.infrastructure.external_services.resend.email_service import ResendEmailService
from src.infrastructure.repositories.customer_repository import CustomerRepository
from src.infrastructure.repositories.invoice_repository import InvoiceRepository
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.store_repository import StoreRepository

logger = get_logger(__name__)


def _generate_invoice_pdf(invoice: Invoice, store_logo_url: str | None) -> bytes:
    """Render the bilingual invoice PDF (sync, run in a thread)."""
    from src.infrastructure.external_services.invoice import InvoicePDFGenerator

    generator = InvoicePDFGenerator(
        template_name="invoice_ar.html",
        language="ar_en",
        store_logo_url=store_logo_url,
    )
    return generator.generate(invoice)


async def handle_invoice_on_order_paid(event: OrderPaidEvent) -> None:
    """Generate + email an ETA invoice when an order becomes PAID.

    Runs on the global event bus. Failures are logged but never raised —
    a failed invoice must not roll back the mark-paid state change that
    triggered us. Merchants can manually reissue via the dashboard if an
    invoice never lands in the customer's inbox.
    """
    log = logger.bind(
        order_id=str(event.order_id),
        order_number=event.order_number,
        store_id=str(event.store_id),
    )

    async with AsyncSessionLocal() as session:
        try:
            created = None
            customer_email = None
            store_name = None
            store_logo_url = None
            store_language = None

            # Single transaction covers the duplicate-check and the write.
            # Starting it here (instead of nesting session.begin() after
            # reads) avoids "A transaction is already begun" — the first
            # session.execute() auto-begins an implicit transaction, which
            # made a later session.begin() illegal.
            async with session.begin():
                # Same per-order lock the merchant "print invoice" path
                # takes: a COD merchant printing at dispatch can race this
                # handler, and without it both would see "no invoice yet"
                # and issue two numbers for one order.
                await lock_order_invoicing(session, event.order_id)
                existing = await session.execute(
                    select(InvoiceModel.id).where(
                        InvoiceModel.order_id == event.order_id
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    log.info("invoice_already_exists_for_order")
                    return

                order_repo = OrderRepository(session)
                store_repo = StoreRepository(session)
                customer_repo = CustomerRepository(session)
                invoice_repo = InvoiceRepository(session)

                order = await order_repo.get_by_id(event.order_id)
                if order is None:
                    log.warning("order_not_found_for_invoice")
                    return

                store = await store_repo.get_by_id(event.store_id)
                if store is None:
                    log.warning("store_not_found_for_invoice")
                    return

                customer = await customer_repo.get_by_id(event.customer_id)
                if customer is None or not getattr(customer, "email", None):
                    log.info("customer_without_email_skipping_send")
                    customer_email = None
                else:
                    customer_email = str(customer.email)

                invoice_number = await invoice_repo.get_next_invoice_number(
                    event.store_id
                )
                # Shared with the merchant "print invoice" path, so the two
                # can never disagree — and so the order's discount reaches
                # the invoice total, which this handler's own copy dropped.
                invoice = invoice_from_order(
                    store,
                    order,
                    invoice_number=invoice_number,
                    buyer_email=customer_email or "",
                )

                # ETA submission when enabled, otherwise the local
                # verification QR — shared with the print path.
                invoice = await apply_tax_authority_step(invoice)

                created = await invoice_repo.create(invoice)
                store_name = store.name
                store_logo_url = store.logo_url
                store_language = store.default_language

            if customer_email and created is not None:
                try:
                    pdf_bytes = await asyncio.to_thread(
                        _generate_invoice_pdf, created, store_logo_url
                    )
                    svc = ResendEmailService()
                    # `send_invoice_email` is not in the merchant-template
                    # registry (the PDF attachment is the payload, not the
                    # body) so it stays on the legacy code path. Pass
                    # store_id forward-compatibly: if a future "invoice
                    # email" event_type is added to the registry, this
                    # call site already routes through the renderer.
                    await svc.send_invoice_email(
                        email=customer_email,
                        order_number=event.order_number,
                        invoice_number=created.invoice_number,
                        pdf_bytes=pdf_bytes,
                        store_name=store_name,
                        language=store_language,
                    )
                    log.info(
                        "invoice_issued_and_emailed",
                        invoice_number=created.invoice_number,
                        to=customer_email,
                    )
                except Exception:
                    log.exception("invoice_email_failed")
            elif created is not None:
                log.info(
                    "invoice_issued_no_email",
                    invoice_number=created.invoice_number,
                )
        except Exception:
            log.exception("invoice_on_paid_handler_failed")
