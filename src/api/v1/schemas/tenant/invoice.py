"""Invoice API schemas for ETA e-invoicing."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.entities.invoice import InvoiceStatus, InvoiceType


# Request schemas
class SellerInfoRequest(BaseModel):
    """Seller information for invoice."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tax_id": "123456789",
                "name": "Nile Fashion LLC",
                "name_ar": "نايل فاشن",
                "branch_id": "0",
                "governorate": "Cairo",
                "city": "Nasr City",
                "street": "Abbas El-Akkad",
                "building_number": "12",
                "activity_code": "4649",
            }
        }
    )

    tax_id: str = Field(..., description="Tax registration number")
    name: str = Field(..., description="Company name")
    name_ar: str | None = Field(None, description="Arabic name")
    branch_id: str = Field(default="0", description="Branch code")
    governorate: str | None = Field(None, description="Governorate")
    city: str | None = Field(None, description="City")
    street: str | None = Field(None, description="Street name")
    building_number: str | None = Field(None, description="Building number")
    activity_code: str = Field(default="4649", description="Business activity code")


class BuyerInfoRequest(BaseModel):
    """Buyer information for invoice."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "buyer_type": "B",
                "tax_id": "987654321",
                "name": "Acme Corp",
                "city": "Alexandria",
                "email": "billing@acme.eg",
            }
        }
    )

    buyer_type: str = Field(
        default="B", description="B=Business, P=Person, F=Foreigner"
    )
    tax_id: str | None = Field(None, description="Tax ID (required for business)")
    national_id: str | None = Field(None, description="National ID (for persons)")
    name: str = Field(description="Buyer name")
    name_ar: str | None = Field(None, description="Buyer name in Arabic")
    governorate: str | None = Field(None, description="Governorate")
    city: str | None = Field(None, description="City")
    street: str | None = Field(None, description="Street")
    building_number: str | None = Field(None, description="Building number")
    phone: str | None = Field(None, description="Phone number")
    email: str | None = Field(None, description="Email address")


class InvoiceLineItemRequest(BaseModel):
    """Invoice line item request."""

    description: str = Field(..., min_length=1)
    description_ar: str | None = None
    item_code: str = Field(..., description="EGS or GS1 product code")
    item_type: str = Field(default="EGS", description="GS1 or EGS")
    unit_type: str = Field(default="EA", description="Unit of measure")
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0, description="Price per unit before tax")
    discount: Decimal = Field(default=Decimal("0"), ge=0)
    vat_rate: Decimal = Field(
        default=Decimal("14.00"), description="VAT rate percentage"
    )
    internal_code: str | None = Field(None, description="Internal SKU")


class CreateInvoiceRequest(BaseModel):
    """Create invoice request."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "invoice_type": "I",
                "seller": {
                    "tax_id": "123456789",
                    "name": "Nile Fashion LLC",
                    "activity_code": "4649",
                },
                "buyer": {
                    "buyer_type": "B",
                    "tax_id": "987654321",
                    "name": "Acme Corp",
                },
                "line_items": [
                    {
                        "description": "Egyptian Cotton T-Shirt",
                        "item_code": "EG-001",
                        "quantity": "2",
                        "unit_price": "250.00",
                        "vat_rate": "14.00",
                    }
                ],
            }
        },
    )

    order_id: UUID | None = Field(None, description="Associated order ID")
    customer_id: UUID | None = Field(None, description="Customer ID")
    invoice_type: InvoiceType = Field(
        default=InvoiceType.INVOICE,
        description="Invoice type: I=Invoice, C=Credit, D=Debit",
    )

    seller: SellerInfoRequest = Field(description="Seller / issuer information")
    buyer: BuyerInfoRequest = Field(description="Buyer / receiver information")

    line_items: list[InvoiceLineItemRequest] = Field(
        ..., min_length=1, description="Invoice line items (at least one)"
    )

    extra_discount: int = Field(default=0, ge=0, description="Extra discount in cents")
    notes: str | None = Field(None, description="Notes in English")
    notes_ar: str | None = Field(None, description="Notes in Arabic")

    # For credit/debit notes
    original_invoice_number: str | None = Field(
        None, description="Original invoice number (required for credit/debit notes)"
    )


class UpdateInvoiceRequest(BaseModel):
    """Update invoice request (only for draft invoices)."""

    buyer: BuyerInfoRequest | None = None
    line_items: list[InvoiceLineItemRequest] | None = None
    extra_discount: int | None = None
    notes: str | None = None
    notes_ar: str | None = None


# Response schemas
class TaxLineResponse(BaseModel):
    """Tax line response."""

    model_config = ConfigDict(from_attributes=True)

    tax_type: str
    amount: Decimal
    rate: Decimal
    sub_type: str = "V009"


class InvoiceLineItemResponse(BaseModel):
    """Invoice line item response."""

    model_config = ConfigDict(from_attributes=True)

    description: str
    description_ar: str | None
    item_type: str
    item_code: str
    unit_type: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    sales_total: Decimal
    net_total: Decimal
    taxes: list[TaxLineResponse]
    total: Decimal
    internal_code: str | None


class SellerInfoResponse(BaseModel):
    """Seller information response."""

    model_config = ConfigDict(from_attributes=True)

    tax_id: str
    name: str
    name_ar: str | None
    branch_id: str
    country: str = "EG"
    governorate: str | None
    city: str | None
    street: str | None
    building_number: str | None
    activity_code: str


class BuyerInfoResponse(BaseModel):
    """Buyer information response."""

    model_config = ConfigDict(from_attributes=True)

    buyer_type: str
    tax_id: str | None
    national_id: str | None
    name: str
    name_ar: str | None
    country: str = "EG"
    governorate: str | None
    city: str | None
    street: str | None
    building_number: str | None
    phone: str | None
    email: str | None


class InvoiceResponse(BaseModel):
    """Invoice response."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "bb0e8400-e29b-41d4-a716-446655440000",
                "store_id": "660e8400-e29b-41d4-a716-446655440000",
                "invoice_number": "INV-2025-0001",
                "invoice_type": "I",
                "status": "submitted",
                "currency": "EGP",
                "subtotal": 50000,
                "total_discount": 0,
                "total_taxes": 7000,
                "extra_discount": 0,
                "total": 57000,
                "total_formatted": "570.00 EGP",
                "created_at": "2025-01-20T09:00:00Z",
                "updated_at": "2025-01-20T09:00:00Z",
            }
        },
    )

    id: UUID = Field(description="Invoice UUID")
    store_id: UUID = Field(description="Store UUID")
    order_id: UUID | None = Field(description="Associated order UUID")
    customer_id: UUID | None = Field(description="Customer UUID")

    invoice_number: str = Field(description="Human-readable invoice number")
    internal_id: str | None = Field(description="Internal reference ID")
    invoice_type: InvoiceType = Field(description="I=Invoice, C=Credit, D=Debit")
    status: InvoiceStatus = Field(description="Invoice status")

    date_issued: datetime = Field(description="Invoice issue date")

    seller: SellerInfoResponse = Field(description="Seller information")
    buyer: BuyerInfoResponse = Field(description="Buyer information")

    currency: str = Field(description="ISO 4217 currency code")
    line_items: list[InvoiceLineItemResponse] = Field(description="Line items")

    # Totals (in cents)
    subtotal: int = Field(description="Subtotal in cents")
    total_discount: int = Field(description="Total discount in cents")
    total_taxes: int = Field(description="Total taxes in cents")
    extra_discount: int = Field(description="Extra discount in cents")
    total: int = Field(description="Grand total in cents")

    # Formatted totals (for display)
    subtotal_formatted: str | None = Field(None, description="Formatted subtotal")
    total_formatted: str | None = Field(None, description="Formatted total")

    # ETA details
    eta_uuid: str | None = Field(description="ETA submission UUID")
    eta_long_id: str | None = Field(description="ETA long ID")
    eta_status_code: str | None = Field(description="ETA status code")
    eta_status_message: str | None = Field(description="ETA status message")
    eta_portal_url: str | None = Field(None, description="Link to ETA portal")

    # QR Code
    qr_code_data: str | None = Field(description="QR code raw data")
    qr_code_image: str | None = Field(description="QR code base64 image")

    # PDF
    pdf_url: str | None = Field(None, description="PDF download URL")

    notes: str | None = Field(description="Notes in English")
    notes_ar: str | None = Field(description="Notes in Arabic")

    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")


class InvoiceListResponse(BaseModel):
    """Invoice list item (simplified)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_number: str
    invoice_type: InvoiceType
    status: InvoiceStatus
    date_issued: datetime
    buyer_name: str
    currency: str
    total: int
    total_formatted: str | None = None
    eta_uuid: str | None
    order_id: UUID | None = None
    created_at: datetime


class SubmitInvoiceResponse(BaseModel):
    """Response after submitting invoice to ETA."""

    success: bool
    invoice_id: UUID
    invoice_number: str
    status: InvoiceStatus
    eta_uuid: str | None = None
    eta_long_id: str | None = None
    eta_portal_url: str | None = None
    error_message: str | None = None
