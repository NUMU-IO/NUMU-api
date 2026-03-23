"""Shared invoice generation helper for payment webhooks.

Called after a successful payment to generate the invoice, PDF, and email.
"""

import asyncio
import logging
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

        # Build buyer info from shipping address
        ship_addr = order.shipping_address or {}
        buyer = BuyerInfo(
            name=f"{ship_addr.get('first_name', '')} {ship_addr.get('last_name', '')}".strip()
            or "Customer",
            phone=ship_addr.get("phone", ""),
            address={
                "city": ship_addr.get("city", ""),
                "street": ship_addr.get("address_line1", ""),
                "country": ship_addr.get("country", "EG"),
            },
        )

        # Build line items
        line_items = []
        for li in order.line_items or []:
            unit_price = li.get("unit_price", 0)
            quantity = li.get("quantity", 1)
            line_items.append({
                "description": li.get("product_name", "Product"),
                "quantity": quantity,
                "unit_price": unit_price,
                "total": unit_price * quantity,
                "vat_rate": 0.14,
                "vat_amount": int(unit_price * quantity * 0.14),
            })

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
            seller=seller.model_dump()
            if hasattr(seller, "model_dump")
            else seller.__dict__,
            buyer=buyer.model_dump()
            if hasattr(buyer, "model_dump")
            else buyer.__dict__,
            line_items=line_items,
            total_amount=order.total,
            currency=order.currency or "EGP",
            eta_uuid=eta_uuid,
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
                        "line_items": line_items,
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
