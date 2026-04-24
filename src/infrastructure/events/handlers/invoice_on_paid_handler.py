"""Generate an invoice when an order's payment is confirmed.

Previously, invoices were created synchronously at checkout for COD orders,
which was wrong: COD means "paid on delivery", so the invoice shouldn't be
issued until the merchant actually marks the order as paid. This handler
moves the trigger to `OrderPaidEvent`, which is fired both by:
  * the merchant-hub "Mark as paid" action (/orders/:id/mark-paid), and
  * any future payment-gateway capture webhook (not yet wired).

The handler is idempotent — if an invoice already exists for the order it
short-circuits. That way re-firing the event (e.g. webhook retry) doesn't
produce duplicate invoices.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from src.config.logging_config import get_logger
from src.core.entities.invoice import (
    BuyerInfo,
    Invoice,
    InvoiceStatus,
    SellerInfo,
)
from src.core.events.order_events import OrderPaidEvent
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.invoice import InvoiceModel
from src.infrastructure.external_services.eta.qr_generator import generate_eta_qr_code
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
                    customer_name = order.shipping_address.first_name or "Customer"
                else:
                    customer_email = str(customer.email)
                    customer_name = customer.full_name or (
                        f"{order.shipping_address.first_name} "
                        f"{order.shipping_address.last_name}".strip()
                    )

                store_address = dict(store.address) if store.address else {}
                store_settings = dict(store.settings) if store.settings else {}
                ship = order.shipping_address

                seller = SellerInfo(
                    tax_id=store_settings.get("tax_id", ""),
                    name=store.name,
                    name_ar=store_settings.get("name_ar", store.name),
                    branch_id=store_settings.get("branch_id", "0"),
                    country=store_address.get("country", "EG"),
                    governorate=store_address.get(
                        "governorate", store_address.get("state", "")
                    ),
                    city=store_address.get("city", ""),
                    street=store_address.get(
                        "street", store_address.get("address_line1", "")
                    ),
                    building_number=store_address.get("building_number", ""),
                    activity_code=store_settings.get("activity_code", "4649"),
                )

                buyer_name = (
                    f"{ship.first_name or ''} {ship.last_name or ''}".strip()
                    or customer_name
                )
                buyer = BuyerInfo(
                    buyer_type="P",
                    name=buyer_name,
                    name_ar=buyer_name,
                    city=ship.city or "",
                    street=ship.address_line1 or "",
                    phone=ship.phone or "",
                    email=customer_email or "",
                )

                invoice_number = await invoice_repo.get_next_invoice_number(
                    event.store_id
                )
                invoice = Invoice(
                    id=uuid4(),
                    store_id=event.store_id,
                    tenant_id=store.tenant_id,
                    order_id=event.order_id,
                    customer_id=event.customer_id,
                    invoice_number=invoice_number,
                    internal_id=event.order_number,
                    status=InvoiceStatus.ACCEPTED,
                    seller=seller,
                    buyer=buyer,
                    currency=order.currency,
                )

                for li in order.line_items:
                    invoice.add_line_item(
                        description=li.product_name,
                        description_ar=li.product_name,
                        item_code=li.sku or "EG-0000-0000",
                        quantity=Decimal(str(li.quantity)),
                        unit_price=Decimal(str(li.unit_price)) / 100,
                        internal_code=li.sku,
                    )

                try:
                    qr_data, qr_image = generate_eta_qr_code(
                        seller_name=seller.name_ar or seller.name,
                        tax_number=seller.tax_id or "",
                        invoice_date=invoice.date_issued,
                        total_with_vat=invoice.total / 100,
                        vat_amount=invoice.total_taxes / 100,
                    )
                    invoice.qr_code_data = qr_data
                    invoice.qr_code_image = qr_image
                except Exception as qr_exc:
                    log.warning("eta_qr_generation_failed", error=str(qr_exc))

                invoice.eta_uuid = f"simulated-{uuid4().hex[:12]}"
                invoice.eta_long_id = f"simulated-long-{uuid4().hex[:20]}"
                invoice.eta_status_code = "accepted"

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
