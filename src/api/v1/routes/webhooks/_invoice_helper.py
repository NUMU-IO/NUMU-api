"""Shared invoice generation helper for payment webhooks.

Called after a successful payment to generate the invoice, PDF, and email.
"""

import asyncio
import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.invoice import BuyerInfo, Invoice, InvoiceStatus, SellerInfo
from src.infrastructure.repositories.invoice_repository import InvoiceRepository
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.store_repository import StoreRepository

logger = logging.getLogger(__name__)


async def generate_invoice_for_paid_order(
    db: AsyncSession,
    order_id,
    store_id,
    tenant_id,
    customer_email: str | None = None,
):
    """Generate invoice for a paid order (called from payment webhooks).

    This is a fire-and-forget background task.
    """
    try:
        order_repo = OrderRepository(db)
        store_repo = StoreRepository(db)
        invoice_repo = InvoiceRepository(db)

        order = await order_repo.get_by_id(order_id)
        if not order:
            logger.warning(f"Invoice generation: order {order_id} not found")
            return

        store = await store_repo.get_by_id(store_id)
        if not store:
            logger.warning(f"Invoice generation: store {store_id} not found")
            return

        # Check if invoice already exists for this order
        existing = await invoice_repo.get_by_order_id(order_id)
        if existing:
            logger.info(f"Invoice already exists for order {order_id}")
            return

        # Build seller info from store settings
        store_settings = store.settings or {}
        invoice_settings = store_settings.get("invoice", {})

        seller = SellerInfo(
            name=store.name,
            name_ar=invoice_settings.get("name_ar", store.name),
            tax_id=invoice_settings.get("tax_id", ""),
            branch_id=invoice_settings.get("branch_id", "0"),
            activity_code=invoice_settings.get("activity_code", "4649"),
            address={
                "governorate": invoice_settings.get("governorate", ""),
                "city": invoice_settings.get("city", ""),
                "street": invoice_settings.get("street", ""),
                "building_number": invoice_settings.get("building_number", ""),
            },
        )

        # Build buyer info from shipping address (can be Pydantic model or dict)
        ship_addr = order.shipping_address
        if ship_addr and hasattr(ship_addr, "first_name"):
            buyer_name = (
                f"{ship_addr.first_name} {ship_addr.last_name}".strip() or "Customer"
            )
            buyer_phone = getattr(ship_addr, "phone", "") or ""
            buyer_city = getattr(ship_addr, "city", "") or ""
            buyer_street = getattr(ship_addr, "address_line1", "") or ""
            buyer_country = getattr(ship_addr, "country", "EG") or "EG"
        elif isinstance(ship_addr, dict):
            buyer_name = (
                f"{ship_addr.get('first_name', '')} {ship_addr.get('last_name', '')}".strip()
                or "Customer"
            )
            buyer_phone = ship_addr.get("phone", "")
            buyer_city = ship_addr.get("city", "")
            buyer_street = ship_addr.get("address_line1", "")
            buyer_country = ship_addr.get("country", "EG")
        else:
            buyer_name, buyer_phone, buyer_city, buyer_street, buyer_country = (
                "Customer",
                "",
                "",
                "",
                "EG",
            )

        buyer = BuyerInfo(
            name=buyer_name,
            phone=buyer_phone,
            address={
                "city": buyer_city,
                "street": buyer_street,
                "country": buyer_country,
            },
        )

        invoice_number = await invoice_repo.get_next_invoice_number(store_id)

        # Simulate ETA QR
        eta_uuid = str(uuid4())

        invoice = Invoice(
            store_id=store_id,
            tenant_id=tenant_id,
            order_id=order_id,
            invoice_number=invoice_number,
            invoice_type="I",
            status=InvoiceStatus.ACCEPTED,
            seller=seller,
            buyer=buyer,
            currency=order.currency or "EGP",
            eta_uuid=eta_uuid,
        )

        # Add line items using Invoice.add_line_item() for proper calculation
        for li in order.line_items or []:
            if isinstance(li, dict):
                unit_price_cents = li.get("unit_price", 0)
                qty = li.get("quantity", 1)
                desc = li.get("product_name", "Product")
                sku = li.get("sku") or li.get("product_id", "ITEM")
            else:
                unit_price_cents = getattr(li, "unit_price", 0)
                qty = getattr(li, "quantity", 1)
                desc = getattr(li, "product_name", "Product")
                sku = getattr(li, "sku", None) or getattr(li, "product_id", "ITEM")

            invoice.add_line_item(
                description=str(desc),
                item_code=str(sku),
                quantity=Decimal(str(qty)),
                unit_price=Decimal(str(unit_price_cents)) / Decimal("100"),
                vat_rate=Decimal("14"),
            )

        created_inv = await invoice_repo.create(invoice)
        logger.info(
            f"Invoice {created_inv.invoice_number} generated for order {order_id}"
        )

        # Generate PDF and email in background
        async def _generate_pdf_and_email():
            try:
                from src.infrastructure.external_services.invoice.pdf_generator import (
                    InvoicePDFGenerator,
                )

                pdf_gen = InvoicePDFGenerator()
                pdf_bytes = await pdf_gen.generate(
                    invoice_data={
                        "invoice_number": created_inv.invoice_number,
                        "seller": seller.__dict__
                        if hasattr(seller, "__dict__")
                        else seller,
                        "buyer": buyer.__dict__
                        if hasattr(buyer, "__dict__")
                        else buyer,
                        "line_items": order.line_items or [],
                        "total_amount": order.total,
                        "currency": order.currency or "EGP",
                        "eta_uuid": eta_uuid,
                        "store_name": store.name,
                        "store_logo_url": store.logo_url,
                    }
                )

                if customer_email and pdf_bytes:
                    from src.infrastructure.external_services.resend.email_service import (
                        ResendEmailService,
                    )

                    email_svc = ResendEmailService()
                    await email_svc.send_invoice_email(
                        email=customer_email,
                        invoice_number=created_inv.invoice_number,
                        order_number=order.order_number,
                        pdf_bytes=pdf_bytes,
                        store_name=store.name,
                        language=store.default_language,
                    )
                    logger.info(
                        f"Invoice {created_inv.invoice_number} emailed to {customer_email}"
                    )
            except Exception as exc:
                logger.warning(f"Invoice PDF/email failed: {exc}")

        asyncio.create_task(_generate_pdf_and_email())

    except Exception as e:
        logger.error(f"Invoice generation failed for order {order_id}: {e}")
