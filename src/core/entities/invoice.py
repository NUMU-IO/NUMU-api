"""Invoice entity for Egyptian e-invoicing (ETA) compliance.

This entity represents a tax invoice compliant with Egyptian Tax
Authority (ETA) e-invoicing requirements. All commercial transactions
in Egypt require electronic invoices submitted to the ETA system.

ETA Requirements:
- Unique invoice number
- Seller and buyer tax registration numbers
- Line items with EGS (Egyptian Tax Code) codes
- VAT calculations (14% standard rate)
- Digital signature and QR code
- Submission within 7 days of issuance

VAT-inclusive pricing model (Egyptian retail standard):
- Merchants enter FINAL prices that already include 14% VAT
- We do NOT add VAT on top at checkout
- VAT is extracted from the subtotal for accounting/ETA reporting:
      vat_amount = subtotal * 14 / 114
      net_amount_before_vat = subtotal - vat_amount
- Customer pays: grand_total = subtotal + shipping_fee
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.entities.base import BaseEntity

# Egyptian VAT rate. Applied as VAT-inclusive — merchants enter the
# final retail price and we extract VAT from it for tax reporting.
DEFAULT_VAT_RATE = Decimal("14.00")


class InvoiceStatus(StrEnum):
    """Invoice status for ETA submission workflow."""

    DRAFT = "draft"  # Not yet submitted
    PENDING = "pending"  # Awaiting submission
    SUBMITTED = "submitted"  # Sent to ETA
    ACCEPTED = "accepted"  # Accepted by ETA
    REJECTED = "rejected"  # Rejected by ETA
    CANCELLED = "cancelled"  # Cancelled invoice


class InvoiceType(StrEnum):
    """Invoice types per ETA classification."""

    INVOICE = "I"  # Regular invoice (فاتورة)
    CREDIT_NOTE = "C"  # Credit note (إشعار دائن)
    DEBIT_NOTE = "D"  # Debit note (إشعار مدين)


class TaxType(StrEnum):
    """Egyptian tax types."""

    VAT = "T1"  # Value Added Tax (14%)
    TABLE_TAX = "T2"  # Table Tax
    STAMP_TAX = "T3"  # Stamp Tax
    WITHHOLDING = "T4"  # Withholding Tax


class SellerInfo(BaseModel):
    """Seller (issuer) information for invoice."""

    model_config = ConfigDict(frozen=True)

    tax_id: str  # Tax registration number (الرقم الضريبي)
    name: str  # Company name
    name_ar: str | None = None  # Arabic name
    branch_id: str = "0"  # Branch code
    country: str = "EG"
    governorate: str | None = None
    city: str | None = None
    street: str | None = None
    building_number: str | None = None
    activity_code: str = "4649"  # Business activity code
    phone: str | None = None


class BuyerInfo(BaseModel):
    """Buyer (receiver) information for invoice."""

    model_config = ConfigDict(frozen=True)

    buyer_type: str = "B"  # B=Business, P=Person, F=Foreigner
    tax_id: str | None = None  # Tax ID (required for business)
    national_id: str | None = None  # National ID (for persons)
    name: str
    name_ar: str | None = None
    country: str = "EG"
    governorate: str | None = None
    city: str | None = None
    street: str | None = None
    building_number: str | None = None
    phone: str | None = None
    email: str | None = None


class TaxLine(BaseModel):
    """Tax calculation line item."""

    model_config = ConfigDict(frozen=True)

    tax_type: TaxType = TaxType.VAT
    amount: Decimal  # Tax amount in EGP (already included in line total)
    sub_type: str = "V009"  # VAT sub-type code
    rate: Decimal = DEFAULT_VAT_RATE  # Tax rate percentage


class InvoiceLineItem(BaseModel):
    """Invoice line item with VAT-inclusive pricing.

    ``unit_price`` is the FINAL retail price including VAT. ``net_total``
    is the VAT-inclusive line total (what the customer pays for this
    line). ``taxes`` records the VAT extracted from ``net_total`` for
    accounting / ETA reporting — VAT is *not* added on top.
    """

    model_config = ConfigDict(frozen=True)

    description: str
    description_ar: str | None = None
    item_type: str = "GS1"  # GS1, EGS (Egyptian Standard)
    item_code: str  # Product code (GS1 barcode or EGS code)
    unit_type: str = "EA"  # Unit of measure (EA=Each, KGM=Kilogram, etc.)
    quantity: Decimal
    unit_price: Decimal  # VAT-inclusive unit price
    discount: Decimal = Decimal("0")  # Discount amount (also VAT-inclusive)
    sales_total: Decimal  # quantity * unit_price (VAT-inclusive)
    net_total: Decimal  # sales_total - discount (VAT-inclusive line total)
    taxes: list[TaxLine] = Field(default_factory=list)
    total: Decimal  # Equals net_total (VAT already inside it)
    internal_code: str | None = None  # Internal SKU


def _round_cents(amount: Decimal) -> int:
    """Round a Decimal EGP amount to cents using banker-safe HALF_UP."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def extract_vat_cents(
    subtotal_cents: int, vat_rate: Decimal = DEFAULT_VAT_RATE
) -> tuple[int, int]:
    """Extract VAT from a VAT-inclusive cents amount.

    Returns ``(vat_amount_cents, net_before_vat_cents)``.

    Formula (VAT-inclusive):
        vat_amount = subtotal * rate / (100 + rate)
        net_amount_before_vat = subtotal - vat_amount

    Example (rate = 14, subtotal = 10000 cents = 100 EGP):
        vat_amount       = 10000 * 14 / 114 ≈ 1228 cents (12.28 EGP)
        net_before_vat   = 10000 - 1228     = 8772 cents (87.72 EGP)
    """
    if subtotal_cents <= 0 or vat_rate <= 0:
        return 0, max(0, subtotal_cents)
    subtotal = Decimal(subtotal_cents)
    vat = subtotal * vat_rate / (Decimal(100) + vat_rate)
    vat_cents = int(vat.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return vat_cents, subtotal_cents - vat_cents


class Invoice(BaseEntity):
    """Invoice entity for Egyptian e-invoicing (VAT-inclusive model).

    This represents a complete invoice document that can be submitted
    to the Egyptian Tax Authority (ETA) e-invoicing system.

    Pricing model:
        * ``subtotal`` is the sum of line ``net_total`` values, which are
          already VAT-inclusive (no VAT added on top).
        * ``vat_amount`` is the 14% VAT *extracted* from ``subtotal`` —
          shown on the invoice for tax reporting but never added to the
          grand total.
        * ``shipping_fee`` is the shipping cost (not VAT-applicable in
          this phase).
        * ``grand_total = subtotal + shipping_fee``  — what the customer
          actually paid.

    Workflow:
        1. Create invoice (DRAFT)
        2. ``calculate_totals()`` derives VAT + net amount + grand total
        3. Submit to ETA (SUBMITTED)
        4. Receive response (ACCEPTED/REJECTED)
    """

    # Identifiers
    store_id: UUID
    tenant_id: UUID | None = None
    order_id: UUID | None = None
    customer_id: UUID | None = None

    # Invoice details
    invoice_number: str  # Unique sequential number
    internal_id: str | None = None  # Internal reference
    invoice_type: InvoiceType = InvoiceType.INVOICE
    status: InvoiceStatus = InvoiceStatus.DRAFT

    # Dates
    date_issued: datetime = Field(default_factory=datetime.utcnow)
    date_time_issued: str | None = None  # ISO format for ETA

    # Parties
    seller: SellerInfo
    buyer: BuyerInfo

    # Currency
    currency: str = "EGP"
    exchange_rate: Decimal = Decimal("1.0")  # For foreign currency

    # Line items
    line_items: list[InvoiceLineItem] = Field(default_factory=list)

    # ── VAT-inclusive pricing model ──────────────────────────────────
    # Prices stored on line items already include VAT. ``vat_amount`` is
    # extracted from ``subtotal`` for tax reporting only — never added
    # to ``grand_total``.
    prices_include_vat: bool = True
    vat_rate: Decimal = DEFAULT_VAT_RATE  # 14% Egyptian standard

    # Totals — all in EGP cents for precision.
    subtotal: int = 0  # Sum of line net_total (VAT-inclusive)
    vat_amount: int = 0  # VAT extracted from subtotal (informational)
    net_amount_before_vat: int = 0  # subtotal - vat_amount
    total_discount: int = 0  # Total line-level discounts
    extra_discount: int = 0  # Invoice-level discount applied after subtotal
    shipping_fee: int = 0  # Shipping cost (VAT-free in current phase)
    grand_total: int = 0  # subtotal + shipping_fee - extra_discount

    # Legacy aliases retained so existing readers (ETA submission,
    # PDF generator's old code paths, response serializers) keep
    # working. ``total_taxes`` mirrors ``vat_amount``; ``total``
    # mirrors ``grand_total``.
    total_taxes: int = 0
    total: int = 0

    # ETA submission details
    eta_uuid: str | None = None  # UUID from ETA after acceptance
    eta_long_id: str | None = None  # Long ID for public access
    eta_submission_id: str | None = None  # Submission batch ID
    eta_internal_id: str | None = None  # ETA internal ID
    eta_hash: str | None = None  # Document hash
    eta_status_code: str | None = None  # Status code from ETA
    eta_status_message: str | None = None  # Status message

    # QR Code
    qr_code_data: str | None = None  # QR code string
    qr_code_image: str | None = None  # Base64 encoded QR image

    # Signature (for digital signing)
    signature: str | None = None
    signature_type: str | None = None
    signature_timestamp: datetime | None = None

    # PDF storage
    pdf_r2_key: str | None = None  # R2 object key (e.g. "documents/abc123.pdf")
    pdf_url: str | None = None  # Public URL for the generated PDF

    # Related documents
    related_invoice_id: UUID | None = None  # For credit/debit notes
    original_invoice_number: str | None = None

    # Metadata
    notes: str | None = None
    notes_ar: str | None = None  # Arabic notes

    def calculate_totals(self) -> None:
        """Recalculate totals from line items (VAT-inclusive model).

        Per Egyptian retail standard: line ``net_total`` is the price
        the customer actually pays (VAT already inside). We sum those
        for the subtotal, then *extract* VAT from that subtotal for
        the invoice's tax-reporting fields. Shipping is added on top.
        """
        subtotal_cents = 0
        total_discount_cents = 0

        for item in self.line_items:
            subtotal_cents += _round_cents(item.net_total)
            total_discount_cents += _round_cents(item.discount)

        vat_cents, net_before_vat_cents = extract_vat_cents(
            subtotal_cents, self.vat_rate
        )

        self.subtotal = subtotal_cents
        self.total_discount = total_discount_cents
        self.vat_amount = vat_cents
        self.net_amount_before_vat = net_before_vat_cents
        # Customer-facing total: subtotal already INCLUDES VAT, so
        # we add ONLY shipping (and subtract any invoice-level extra
        # discount). VAT is NOT added on top.
        self.grand_total = subtotal_cents + self.shipping_fee - self.extra_discount

        # Legacy fields kept in sync so any older reader (ETA
        # submission, dashboards) still gets sensible values.
        self.total_taxes = vat_cents
        self.total = self.grand_total

        self.touch()

    def add_line_item(
        self,
        description: str,
        item_code: str,
        quantity: Decimal,
        unit_price: Decimal,
        discount: Decimal = Decimal("0"),
        vat_rate: Decimal | None = None,
        description_ar: str | None = None,
        item_type: str = "EGS",
        unit_type: str = "EA",
        internal_code: str | None = None,
    ) -> None:
        """Add a line item using VAT-inclusive pricing.

        ``unit_price`` is the FINAL retail price (including VAT). The
        per-line ``TaxLine.amount`` records the VAT *extracted* from
        the inclusive line total so ETA submission still has a per-line
        tax breakdown, but ``total = net_total`` (VAT is *not* added
        on top).

        Args:
            description: Item description in English
            item_code: GS1 or EGS product code
            quantity: Quantity
            unit_price: VAT-inclusive unit price
            discount: Discount amount (also VAT-inclusive)
            vat_rate: VAT rate (default 14%) — only used for per-line
                informational breakdown; the invoice-level extraction
                uses ``self.vat_rate``.
            description_ar: Arabic description
            item_type: Item code type (GS1, EGS)
            unit_type: Unit of measure
            internal_code: Internal SKU
        """
        effective_rate = vat_rate if vat_rate is not None else self.vat_rate

        sales_total = quantity * unit_price  # VAT-inclusive
        net_total = sales_total - discount  # VAT-inclusive line total

        # Per-line VAT extracted from the inclusive net_total.
        if effective_rate > 0 and net_total > 0:
            vat_amount = net_total * effective_rate / (Decimal(100) + effective_rate)
        else:
            vat_amount = Decimal("0")

        tax_line = TaxLine(
            tax_type=TaxType.VAT,
            amount=vat_amount,
            rate=effective_rate,
        )

        # ``total`` mirrors ``net_total`` — prices are VAT-inclusive,
        # so we don't add VAT on top.
        line_item = InvoiceLineItem(
            description=description,
            description_ar=description_ar,
            item_type=item_type,
            item_code=item_code,
            unit_type=unit_type,
            quantity=quantity,
            unit_price=unit_price,
            discount=discount,
            sales_total=sales_total,
            net_total=net_total,
            taxes=[tax_line],
            total=net_total,
            internal_code=internal_code,
        )

        self.line_items = [*self.line_items, line_item]
        self.calculate_totals()

    def to_eta_format(self) -> dict[str, Any]:
        """Convert invoice to ETA API format.

        ETA's e-invoicing schema expects pre-tax values: ``salesTotal``,
        ``netTotal``, and per-line ``taxableItems[].amount``. Because
        our retail model is VAT-inclusive, we expose the *extracted*
        pre-tax components so the ETA portal computes consistent
        figures.

        Returns:
            Dictionary in ETA submission format.
        """
        # Format date-time for ETA
        date_time_issued = self.date_issued.strftime("%Y-%m-%dT%H:%M:%SZ")

        invoice_lines = []
        for item in self.line_items:
            line_vat = sum((t.amount for t in item.taxes), Decimal("0"))
            line_net_before_vat = item.net_total - line_vat
            line_sales_before_vat = item.sales_total - (
                item.sales_total * self.vat_rate / (Decimal(100) + self.vat_rate)
                if self.vat_rate > 0
                else Decimal("0")
            )
            unit_price_before_vat = (
                item.unit_price / (Decimal(1) + self.vat_rate / Decimal(100))
                if self.vat_rate > 0
                else item.unit_price
            )

            invoice_lines.append({
                "description": item.description,
                "itemType": item.item_type,
                "itemCode": item.item_code,
                "unitType": item.unit_type,
                "quantity": float(item.quantity),
                "unitValue": {
                    "currencySold": self.currency,
                    "amountEGP": float(unit_price_before_vat),
                },
                "salesTotal": float(line_sales_before_vat),
                "discount": {
                    "rate": 0,
                    "amount": float(item.discount),
                },
                "netTotal": float(line_net_before_vat),
                "taxableItems": [
                    {
                        "taxType": tax.tax_type.value,
                        "amount": float(tax.amount),
                        "subType": tax.sub_type,
                        "rate": float(tax.rate),
                    }
                    for tax in item.taxes
                ],
                "total": float(item.total),  # Customer-facing (VAT-inclusive)
                "internalCode": item.internal_code or "",
            })

        return {
            "issuer": {
                "type": "B",
                "id": self.seller.tax_id,
                "name": self.seller.name,
                "address": {
                    "country": self.seller.country,
                    "governate": self.seller.governorate or "",
                    "regionCity": self.seller.city or "",
                    "street": self.seller.street or "",
                    "buildingNumber": self.seller.building_number or "",
                },
                "branchID": self.seller.branch_id,
            },
            "receiver": {
                "type": self.buyer.buyer_type,
                "id": self.buyer.tax_id or self.buyer.national_id or "",
                "name": self.buyer.name,
                "address": {
                    "country": self.buyer.country,
                    "governate": self.buyer.governorate or "",
                    "regionCity": self.buyer.city or "",
                    "street": self.buyer.street or "",
                    "buildingNumber": self.buyer.building_number or "",
                },
            },
            "documentType": self.invoice_type.value,
            "documentTypeVersion": "1.0",
            "dateTimeIssued": date_time_issued,
            "taxpayerActivityCode": self.seller.activity_code,
            "internalID": self.internal_id or str(self.id),
            "invoiceLines": invoice_lines,
            # Pre-tax totals for ETA reporting.
            "totalSalesAmount": self.net_amount_before_vat / 100,
            "totalDiscountAmount": self.total_discount / 100,
            "netAmount": (self.net_amount_before_vat - self.total_discount) / 100,
            "taxTotals": [
                {
                    "taxType": "T1",
                    "amount": self.vat_amount / 100,
                }
            ],
            # Customer-facing grand total (VAT-inclusive + shipping).
            "totalAmount": self.grand_total / 100,
            "extraDiscountAmount": self.extra_discount / 100,
        }

    @property
    def is_editable(self) -> bool:
        """Check if invoice can still be edited."""
        return self.status in (InvoiceStatus.DRAFT, InvoiceStatus.REJECTED)

    @property
    def is_submitted(self) -> bool:
        """Check if invoice has been submitted to ETA."""
        return self.status in (
            InvoiceStatus.SUBMITTED,
            InvoiceStatus.ACCEPTED,
        )

    @property
    def eta_portal_url(self) -> str | None:
        """Get URL to view invoice on ETA portal."""
        if self.eta_uuid and self.eta_long_id:
            return (
                f"https://invoicing.eta.gov.eg/print/{self.eta_uuid}/{self.eta_long_id}"
            )
        return None
