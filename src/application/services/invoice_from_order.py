"""Build an Invoice from an Order — the one place that mapping lives.

It used to be written out twice (the on-paid event handler and the lazy
"get-or-create" route), and both copies had the same holes:

* the order's discount never reached the invoice, so a coupon or automatic
  offer left the invoice's grand total ABOVE what the customer paid — on a
  cash-on-delivery order, the paper in the box asked for more money than the
  courier was told to collect;
* the variant was dropped from every line ("T-shirt" rather than
  "T-shirt — Red / L"), which is the detail a packer and a customer check;
* the buyer's address lost its second line and governorate.

`invoice_from_order` is pure — the caller supplies the sequential invoice
number and persists. `apply_tax_authority_step` and `lock_order_invoicing` are
the two I/O steps every issuing path shares, kept here so they cannot drift.
"""

import logging
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.invoice import BuyerInfo, Invoice, InvoiceStatus, SellerInfo
from src.core.entities.order import Order
from src.core.entities.store import Store

# Placeholder EGS item code for products with no SKU. ETA requires SOME code
# on every line; a merchant who is not ETA-registered never sees it used.
_FALLBACK_ITEM_CODE = "EG-0000-0000"

logger = logging.getLogger(__name__)


async def lock_order_invoicing(session: AsyncSession, order_id: UUID) -> None:
    """Serialize every "issue an invoice for this order" within a transaction.

    Nothing in the schema stops one order getting two invoices, and issuing
    is now a routine merchant action (print at dispatch) that can race itself
    on a double click, or race the on-paid handler. Both paths take this lock
    and then re-check for an existing invoice, so the loser finds the
    winner's invoice instead of minting a second number.

    ponytail: a transaction-scoped advisory lock, released on commit. The
    durable guard is a partial UNIQUE index on invoices(order_id) for
    original invoices — add it once any historical duplicates are cleaned,
    or the migration fails on prod.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"invoice-for-order:{order_id}"},
    )


def seller_from_store(store: Store) -> SellerInfo:
    """Seller details, read in the same precedence the settings API uses.

    `settings["invoice"]` is what the Invoice tax settings card writes; the
    top-level keys are its legacy mirror; the store address is the fallback
    for a merchant who never opened that card.
    """
    settings = dict(store.settings) if store.settings else {}
    invoice_cfg = settings.get("invoice") or {}
    address = dict(store.address) if store.address else {}

    def pick(key: str, *fallbacks: str, default: str = "") -> str:
        if invoice_cfg.get(key):
            return str(invoice_cfg[key])
        if settings.get(key):
            return str(settings[key])
        for fb in fallbacks:
            if address.get(fb):
                return str(address[fb])
        return default

    return SellerInfo(
        tax_id=pick("tax_id"),
        name=store.name,
        name_ar=pick("name_ar", default=store.name),
        branch_id=pick("branch_id", default="0"),
        country=address.get("country", "EG"),
        governorate=pick("governorate", "governorate", "state"),
        city=pick("city", "city"),
        street=pick("street", "street", "address_line1"),
        building_number=pick("building_number", "building_number"),
        activity_code=pick("activity_code", default="4649"),
        # `Store` has no `phone` attribute — it is `contact_phone`. Reading
        # the wrong name left every invoice without the seller's phone,
        # which is how a customer holding the paper reaches the shop.
        phone=settings.get("phone") or getattr(store, "contact_phone", None),
        email=getattr(store, "contact_email", None),
    )


def buyer_from_order(order: Order, email: str = "") -> BuyerInfo:
    ship = order.shipping_address
    name = f"{ship.first_name or ''} {ship.last_name or ''}".strip() or "Customer"
    street = ", ".join(p for p in (ship.address_line1, ship.address_line2) if p)
    return BuyerInfo(
        buyer_type="P",
        name=name,
        name_ar=name,
        governorate=ship.state or "",
        city=ship.city or "",
        street=street,
        phone=ship.phone or "",
        email=email,
    )


def invoice_from_order(
    store: Store,
    order: Order,
    *,
    invoice_number: str,
    status: InvoiceStatus = InvoiceStatus.ACCEPTED,
    buyer_email: str = "",
) -> Invoice:
    """An unsaved Invoice whose grand total equals what the order charges."""
    invoice = Invoice(
        id=uuid4(),
        store_id=store.id,
        tenant_id=store.tenant_id,
        order_id=order.id,
        customer_id=order.customer_id,
        invoice_number=invoice_number,
        internal_id=order.order_number,
        status=status,
        seller=seller_from_store(store),
        buyer=buyer_from_order(order, buyer_email),
        currency=order.currency,
        shipping_fee=order.shipping_cost or 0,
        # The order's discount — coupon and automatic offers together —
        # as one invoice-level discount, which is how the order records it.
        # Set BEFORE the lines so every recalculation already includes it.
        extra_discount=order.discount_amount or 0,
        prices_include_vat=True,
    )

    for li in order.line_items:
        name = li.product_name
        if li.variant_name:
            name = f"{name} — {li.variant_name}"
        invoice.add_line_item(
            description=name,
            description_ar=name,
            item_code=li.sku or _FALLBACK_ITEM_CODE,
            quantity=Decimal(str(li.quantity)),
            unit_price=Decimal(str(li.unit_price)) / 100,
            internal_code=li.sku,
        )
    # An order with no lines still needs its totals computed once.
    invoice.calculate_totals()
    return invoice


async def apply_tax_authority_step(invoice: Invoice) -> Invoice:
    """Submit to ETA when enabled; otherwise stamp the local verification QR.

    Both issuing paths (on-paid handler, merchant print) run this, so an
    invoice printed at dispatch carries the same QR as one issued on payment.
    Only the handler used to generate it, which meant a COD invoice printed
    before payment went out WITHOUT the QR — and for a Saudi store that QR
    (seller, VAT number, time, total, VAT) is required on every simplified
    tax invoice.

    Never raises: a tax-authority outage must not stop a merchant printing
    the paper for a parcel. The invoice lands REJECTED and can be retried.
    """
    from src.infrastructure.external_services.eta.invoice_service import (
        ETAInvoiceService,
    )
    from src.infrastructure.external_services.eta.qr_generator import (
        generate_eta_qr_code,
    )

    eta_svc = ETAInvoiceService()
    if eta_svc.enabled:
        try:
            return await eta_svc.process_invoice_submission(invoice)
        except Exception as exc:  # noqa: BLE001 — never block issuing on ETA
            logger.warning(
                "eta_submission_failed",
                extra={"order_id": str(invoice.order_id), "error": str(exc)},
            )
            invoice.status = InvoiceStatus.REJECTED
            invoice.eta_status_code = "error"
            invoice.eta_status_message = str(exc)[:500]
            return invoice

    try:
        qr_data, qr_image = generate_eta_qr_code(
            seller_name=invoice.seller.name_ar or invoice.seller.name,
            tax_number=invoice.seller.tax_id or "",
            invoice_date=invoice.date_issued,
            total_with_vat=invoice.grand_total / 100,
            vat_amount=invoice.vat_amount / 100,
        )
        invoice.qr_code_data = qr_data
        invoice.qr_code_image = qr_image
    except Exception as exc:  # noqa: BLE001 — a missing QR must not block issuing
        logger.warning(
            "eta_qr_generation_failed",
            extra={"order_id": str(invoice.order_id), "error": str(exc)},
        )
    # Simulated identifiers keep the hub's filters treating this row like a
    # submitted one. The PDF tells them apart (`simulated-` prefix) and never
    # claims tax-authority certification for them.
    invoice.eta_uuid = f"simulated-{uuid4().hex[:12]}"
    invoice.eta_long_id = f"simulated-long-{uuid4().hex[:20]}"
    invoice.eta_status_code = "accepted"
    return invoice
