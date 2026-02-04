"""Unit tests for Invoice entity."""

from decimal import Decimal
from uuid import uuid4

from src.core.entities.invoice import (
    BuyerInfo,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    InvoiceType,
    SellerInfo,
    TaxLine,
    TaxType,
)


class TestInvoiceEntity:
    """Tests for Invoice entity."""

    def _create_seller(self) -> SellerInfo:
        """Create test seller info."""
        return SellerInfo(
            tax_id="123456789",
            name="Test Store",
            name_ar="متجر تجريبي",
            governorate="Cairo",
            city="Nasr City",
            street="Test Street 123",
        )

    def _create_buyer(self, with_tax_id: bool = False) -> BuyerInfo:
        """Create test buyer info."""
        return BuyerInfo(
            buyer_type="B" if with_tax_id else "P",
            tax_id="987654321" if with_tax_id else None,
            name="Test Customer",
            name_ar="عميل تجريبي",
            governorate="Giza",
            city="6th October",
            phone="+201234567890",
        )

    def _create_line_item(
        self,
        description: str = "Test Product",
        quantity: Decimal = Decimal("2"),
        unit_price: Decimal = Decimal("100.00"),
        discount: Decimal = Decimal("0"),
        vat_rate: Decimal = Decimal("14.00"),
    ) -> InvoiceLineItem:
        """Create test line item with tax calculation."""
        sales_total = quantity * unit_price
        net_total = sales_total - discount
        vat_amount = net_total * (vat_rate / Decimal("100"))
        total = net_total + vat_amount

        return InvoiceLineItem(
            description=description,
            item_code="EGS-001",
            quantity=quantity,
            unit_price=unit_price,
            discount=discount,
            sales_total=sales_total,
            net_total=net_total,
            taxes=[
                TaxLine(
                    tax_type=TaxType.VAT,
                    amount=vat_amount,
                    rate=vat_rate,
                )
            ],
            total=total,
        )

    def test_create_invoice(self):
        """Test creating an invoice."""
        store_id = uuid4()
        order_id = uuid4()

        invoice = Invoice(
            id=uuid4(),
            store_id=store_id,
            order_id=order_id,
            invoice_number="INV-2024-0001",
            status=InvoiceStatus.DRAFT,
            seller=self._create_seller(),
            buyer=self._create_buyer(),
            line_items=[],
            subtotal=0,
            total_taxes=0,
            total=0,
        )

        assert invoice.invoice_number == "INV-2024-0001"
        assert invoice.status == InvoiceStatus.DRAFT
        assert invoice.seller.tax_id == "123456789"
        assert invoice.currency == "EGP"

    def test_invoice_with_line_items(self):
        """Test invoice with line items."""
        line_items = [
            self._create_line_item(
                description="Product A",
                quantity=Decimal("2"),
                unit_price=Decimal("100.00"),
            ),
            self._create_line_item(
                description="Product B",
                quantity=Decimal("1"),
                unit_price=Decimal("50.00"),
                discount=Decimal("5.00"),
            ),
        ]

        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            order_id=uuid4(),
            invoice_number="INV-2024-0002",
            status=InvoiceStatus.DRAFT,
            seller=self._create_seller(),
            buyer=self._create_buyer(),
            line_items=line_items,
        )

        # Recalculate totals
        invoice.calculate_totals()

        assert len(invoice.line_items) == 2
        assert invoice.subtotal > 0
        assert invoice.total_taxes > 0
        assert invoice.total > 0

    def test_add_line_item(self):
        """Test adding line item with automatic tax calculation."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0003",
            seller=self._create_seller(),
            buyer=self._create_buyer(),
        )

        invoice.add_line_item(
            description="Test Product",
            item_code="EGS-001",
            quantity=Decimal("3"),
            unit_price=Decimal("100.00"),
            discount=Decimal("10.00"),
            vat_rate=Decimal("14.00"),
        )

        assert len(invoice.line_items) == 1
        item = invoice.line_items[0]
        assert item.sales_total == Decimal("300.00")  # 3 * 100
        assert item.net_total == Decimal("290.00")  # 300 - 10
        assert item.taxes[0].amount == Decimal("40.60")  # 290 * 0.14

    def test_invoice_status_draft(self):
        """Test invoice draft status is editable."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0004",
            status=InvoiceStatus.DRAFT,
            seller=self._create_seller(),
            buyer=self._create_buyer(),
        )

        assert invoice.is_editable is True
        assert invoice.is_submitted is False

    def test_invoice_status_accepted(self):
        """Test accepted invoice is not editable."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0005",
            status=InvoiceStatus.ACCEPTED,
            seller=self._create_seller(),
            buyer=self._create_buyer(),
        )

        assert invoice.is_editable is False
        assert invoice.is_submitted is True

    def test_invoice_status_rejected_is_editable(self):
        """Test rejected invoice is editable."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0006",
            status=InvoiceStatus.REJECTED,
            seller=self._create_seller(),
            buyer=self._create_buyer(),
        )

        assert invoice.is_editable is True

    def test_invoice_with_eta_fields(self):
        """Test invoice with ETA submission fields."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0007",
            status=InvoiceStatus.ACCEPTED,
            seller=self._create_seller(),
            buyer=self._create_buyer(),
            eta_uuid="ETA-UUID-123456",
            eta_long_id="ETA-LONG-ID-789",
            qr_code_data="base64-encoded-qr-data",
        )

        assert invoice.eta_uuid == "ETA-UUID-123456"
        assert invoice.eta_long_id == "ETA-LONG-ID-789"
        assert invoice.qr_code_data is not None

    def test_eta_portal_url(self):
        """Test ETA portal URL generation."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0008",
            status=InvoiceStatus.ACCEPTED,
            seller=self._create_seller(),
            buyer=self._create_buyer(),
            eta_uuid="uuid-123",
            eta_long_id="longid-456",
        )

        url = invoice.eta_portal_url
        assert url is not None
        assert "uuid-123" in url
        assert "longid-456" in url
        assert "invoicing.eta.gov.eg" in url

    def test_eta_portal_url_without_eta_data(self):
        """Test ETA portal URL is None without ETA data."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0009",
            seller=self._create_seller(),
            buyer=self._create_buyer(),
        )

        assert invoice.eta_portal_url is None

    def test_invoice_type_default(self):
        """Test default invoice type is regular invoice."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0010",
            seller=self._create_seller(),
            buyer=self._create_buyer(),
        )

        assert invoice.invoice_type == InvoiceType.INVOICE

    def test_invoice_credit_note(self):
        """Test creating a credit note."""
        original_invoice_id = uuid4()

        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="CN-2024-0001",
            invoice_type=InvoiceType.CREDIT_NOTE,
            seller=self._create_seller(),
            buyer=self._create_buyer(),
            related_invoice_id=original_invoice_id,
            original_invoice_number="INV-2024-0001",
        )

        assert invoice.invoice_type == InvoiceType.CREDIT_NOTE
        assert invoice.related_invoice_id == original_invoice_id

    def test_to_eta_format(self):
        """Test converting invoice to ETA API format."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0011",
            seller=self._create_seller(),
            buyer=self._create_buyer(),
            line_items=[self._create_line_item()],
        )
        invoice.calculate_totals()

        eta_format = invoice.to_eta_format()

        assert "issuer" in eta_format
        assert "receiver" in eta_format
        assert "invoiceLines" in eta_format
        assert "totalAmount" in eta_format

        # Check issuer
        assert eta_format["issuer"]["id"] == "123456789"
        assert eta_format["issuer"]["name"] == "Test Store"

        # Check line items
        assert len(eta_format["invoiceLines"]) == 1

    def test_buyer_b2b(self):
        """Test B2B invoice with buyer tax ID."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0012",
            seller=self._create_seller(),
            buyer=self._create_buyer(with_tax_id=True),
        )

        assert invoice.buyer.tax_id == "987654321"
        assert invoice.buyer.buyer_type == "B"

    def test_buyer_b2c(self):
        """Test B2C invoice without buyer tax ID."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0013",
            seller=self._create_seller(),
            buyer=self._create_buyer(with_tax_id=False),
        )

        assert invoice.buyer.tax_id is None
        assert invoice.buyer.buyer_type == "P"

    def test_calculate_totals(self):
        """Test total calculation from line items."""
        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            invoice_number="INV-2024-0014",
            seller=self._create_seller(),
            buyer=self._create_buyer(),
        )

        # Add items
        invoice.add_line_item(
            description="Product 1",
            item_code="EGS-001",
            quantity=Decimal("2"),
            unit_price=Decimal("100.00"),
        )
        invoice.add_line_item(
            description="Product 2",
            item_code="EGS-002",
            quantity=Decimal("1"),
            unit_price=Decimal("50.00"),
        )

        # Totals should be calculated
        # Product 1: 200 net + 28 VAT = 228
        # Product 2: 50 net + 7 VAT = 57
        # Total: 250 net + 35 VAT = 285
        assert invoice.subtotal == 25000  # 250 * 100 cents
        assert invoice.total_taxes == 3500  # 35 * 100 cents
        assert invoice.total == 28500  # 285 * 100 cents
