"""Invoice API schemas for ETA e-invoicing."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.entities.invoice import InvoiceStatus, InvoiceType


# Request schemas
class SellerInfoRequest(BaseModel):
    """Seller information for invoice."""

    tax_id: str = Field(..., description="Tax registration number")
    name: str = Field(..., description="Company name")
    name_ar: str | None = Field(None, description="Arabic name")
    branch_id: str = Field(default="0", description="Branch code")
    governorate: str | None = None
    city: str | None = None
    street: str | None = None
    building_number: str | None = None
    activity_code: str = Field(default="4649", description="Business activity code")


class BuyerInfoRequest(BaseModel):
    """Buyer information for invoice."""

    buyer_type: str = Field(
        default="B", description="B=Business, P=Person, F=Foreigner"
    )
    tax_id: str | None = Field(None, description="Tax ID (required for business)")
    national_id: str | None = Field(None, description="National ID (for persons)")
    name: str
    name_ar: str | None = None
    governorate: str | None = None
    city: str | None = None
    street: str | None = None
    building_number: str | None = None
    phone: str | None = None
    email: str | None = None


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

    model_config = ConfigDict(str_strip_whitespace=True)

    order_id: UUID | None = Field(None, description="Associated order ID")
    customer_id: UUID | None = Field(None, description="Customer ID")
    invoice_type: InvoiceType = Field(default=InvoiceType.INVOICE)

    seller: SellerInfoRequest
    buyer: BuyerInfoRequest

    line_items: list[InvoiceLineItemRequest] = Field(..., min_length=1)

    extra_discount: int = Field(default=0, ge=0, description="Extra discount in cents")
    notes: str | None = None
    notes_ar: str | None = None

    # For credit/debit notes
    original_invoice_number: str | None = None


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

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    order_id: UUID | None
    customer_id: UUID | None

    invoice_number: str
    internal_id: str | None
    invoice_type: InvoiceType
    status: InvoiceStatus

    date_issued: datetime

    seller: SellerInfoResponse
    buyer: BuyerInfoResponse

    currency: str
    line_items: list[InvoiceLineItemResponse]

    # Totals (in cents)
    subtotal: int
    total_discount: int
    total_taxes: int
    extra_discount: int
    total: int

    # Formatted totals (for display)
    subtotal_formatted: str | None = None
    total_formatted: str | None = None

    # ETA details
    eta_uuid: str | None
    eta_long_id: str | None
    eta_status_code: str | None
    eta_status_message: str | None
    eta_portal_url: str | None = None

    # QR Code
    qr_code_data: str | None
    qr_code_image: str | None

    # PDF
    pdf_url: str | None = None

    notes: str | None
    notes_ar: str | None

    created_at: datetime
    updated_at: datetime


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
