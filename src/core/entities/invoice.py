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
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.entities.base import BaseEntity


class InvoiceStatus(str, Enum):
    """Invoice status for ETA submission workflow."""

    DRAFT = "draft"  # Not yet submitted
    PENDING = "pending"  # Awaiting submission
    SUBMITTED = "submitted"  # Sent to ETA
    ACCEPTED = "accepted"  # Accepted by ETA
    REJECTED = "rejected"  # Rejected by ETA
    CANCELLED = "cancelled"  # Cancelled invoice


class InvoiceType(str, Enum):
    """Invoice types per ETA classification."""

    INVOICE = "I"  # Regular invoice (فاتورة)
    CREDIT_NOTE = "C"  # Credit note (إشعار دائن)
    DEBIT_NOTE = "D"  # Debit note (إشعار مدين)


class TaxType(str, Enum):
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
    amount: Decimal  # Tax amount in EGP
    sub_type: str = "V009"  # VAT sub-type code
    rate: Decimal = Decimal("14.00")  # Tax rate percentage


class InvoiceLineItem(BaseModel):
    """Invoice line item with tax details."""

    model_config = ConfigDict(frozen=True)

    description: str
    description_ar: str | None = None
    item_type: str = "GS1"  # GS1, EGS (Egyptian Standard)
    item_code: str  # Product code (GS1 barcode or EGS code)
    unit_type: str = "EA"  # Unit of measure (EA=Each, KGM=Kilogram, etc.)
    quantity: Decimal
    unit_price: Decimal  # Price per unit (before tax)
    discount: Decimal = Decimal("0")  # Discount amount
    sales_total: Decimal  # quantity * unit_price
    net_total: Decimal  # sales_total - discount
    taxes: list[TaxLine] = Field(default_factory=list)
    total: Decimal  # net_total + sum(taxes)
    internal_code: str | None = None  # Internal SKU


class Invoice(BaseEntity):
    """Invoice entity for Egyptian e-invoicing.

    This represents a complete invoice document that can be submitted
    to the Egyptian Tax Authority (ETA) e-invoicing system.

    Workflow:
    1. Create invoice (DRAFT)
    2. Calculate totals and validate
    3. Submit to ETA (SUBMITTED)
    4. Receive response (ACCEPTED/REJECTED)
    """

    # Identifiers
    store_id: UUID
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

    # Totals (all in EGP cents for precision)
    subtotal: int = 0  # Sum of net_total for all items
    total_discount: int = 0  # Total discounts
    total_taxes: int = 0  # Total tax amount
    extra_discount: int = 0  # Additional invoice-level discount
    total: int = 0  # Final total

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

    # Related documents
    related_invoice_id: UUID | None = None  # For credit/debit notes
    original_invoice_number: str | None = None

    # Metadata
    notes: str | None = None
    notes_ar: str | None = None  # Arabic notes

    def calculate_totals(self) -> None:
        """Recalculate all totals from line items."""
        subtotal = 0
        total_discount = 0
        total_taxes = 0

        for item in self.line_items:
            subtotal += int(item.net_total * 100)
            total_discount += int(item.discount * 100)
            for tax in item.taxes:
                total_taxes += int(tax.amount * 100)

        self.subtotal = subtotal
        self.total_discount = total_discount
        self.total_taxes = total_taxes
        self.total = subtotal + total_taxes - self.extra_discount
        self.touch()

    def add_line_item(
        self,
        description: str,
        item_code: str,
        quantity: Decimal,
        unit_price: Decimal,
        discount: Decimal = Decimal("0"),
        vat_rate: Decimal = Decimal("14.00"),
        description_ar: str | None = None,
        item_type: str = "EGS",
        unit_type: str = "EA",
        internal_code: str | None = None,
    ) -> None:
        """Add a line item with automatic tax calculation.

        Args:
            description: Item description in English
            item_code: GS1 or EGS product code
            quantity: Quantity
            unit_price: Unit price before tax
            discount: Discount amount
            vat_rate: VAT rate (default 14%)
            description_ar: Arabic description
            item_type: Item code type (GS1, EGS)
            unit_type: Unit of measure
            internal_code: Internal SKU
        """
        sales_total = quantity * unit_price
        net_total = sales_total - discount
        vat_amount = net_total * (vat_rate / Decimal("100"))
        total = net_total + vat_amount

        tax_line = TaxLine(
            tax_type=TaxType.VAT,
            amount=vat_amount,
            rate=vat_rate,
        )

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
            total=total,
            internal_code=internal_code,
        )

        self.line_items = [*self.line_items, line_item]
        self.calculate_totals()

    def to_eta_format(self) -> dict[str, Any]:
        """Convert invoice to ETA API format.

        Returns:
            Dictionary in ETA submission format
        """
        # Format date-time for ETA
        date_time_issued = self.date_issued.strftime("%Y-%m-%dT%H:%M:%SZ")

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
            "invoiceLines": [
                {
                    "description": item.description,
                    "itemType": item.item_type,
                    "itemCode": item.item_code,
                    "unitType": item.unit_type,
                    "quantity": float(item.quantity),
                    "unitValue": {
                        "currencySold": self.currency,
                        "amountEGP": float(item.unit_price),
                    },
                    "salesTotal": float(item.sales_total),
                    "discount": {
                        "rate": 0,
                        "amount": float(item.discount),
                    },
                    "netTotal": float(item.net_total),
                    "taxableItems": [
                        {
                            "taxType": tax.tax_type.value,
                            "amount": float(tax.amount),
                            "subType": tax.sub_type,
                            "rate": float(tax.rate),
                        }
                        for tax in item.taxes
                    ],
                    "total": float(item.total),
                    "internalCode": item.internal_code or "",
                }
                for item in self.line_items
            ],
            "totalSalesAmount": self.subtotal / 100,
            "totalDiscountAmount": self.total_discount / 100,
            "netAmount": (self.subtotal - self.total_discount) / 100,
            "taxTotals": [
                {
                    "taxType": "T1",
                    "amount": self.total_taxes / 100,
                }
            ],
            "totalAmount": self.total / 100,
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
            return f"https://invoicing.eta.gov.eg/print/{self.eta_uuid}/{self.eta_long_id}"
        return None
