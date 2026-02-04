"""Unit tests for Egyptian tax service."""

from decimal import Decimal

from src.core.interfaces.services.tax_service import TaxRateType
from src.infrastructure.external_services.tax.egyptian_tax_service import (
    EGYPT_EXEMPT_RATE,
    EGYPT_STANDARD_RATE,
    EGYPT_ZERO_RATE,
    EgyptianTaxService,
)


class TestEgyptianTaxService:
    """Tests for Egyptian tax service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = EgyptianTaxService()

    def test_country_code(self):
        """Test country code is EG."""
        assert self.service.country_code == "EG"

    def test_standard_rate(self):
        """Test standard VAT rate is 14%."""
        rate = self.service.standard_rate
        assert rate.rate == Decimal("14.00")
        assert rate.type == TaxRateType.STANDARD
        assert rate.code == "T1"

    def test_get_rate_for_standard_product(self):
        """Test getting rate for standard product."""
        rate = self.service.get_rate_for_product("PROD-001")
        assert rate == EGYPT_STANDARD_RATE

    def test_get_rate_for_export(self):
        """Test getting zero rate for exports."""
        rate = self.service.get_rate_for_product("PROD-001", category="exports")
        assert rate == EGYPT_ZERO_RATE
        assert rate.rate == Decimal("0.00")

    def test_get_rate_for_exempt_services(self):
        """Test getting exempt rate for financial services."""
        rate = self.service.get_rate_for_product("SVC-001", category="financial_services")
        assert rate == EGYPT_EXEMPT_RATE

    def test_get_rate_for_healthcare(self):
        """Test healthcare is exempt."""
        rate = self.service.get_rate_for_product("HC-001", category="healthcare")
        assert rate == EGYPT_EXEMPT_RATE

    def test_calculate_tax_standard(self):
        """Test calculating standard 14% VAT."""
        result = self.service.calculate_tax(Decimal("100.00"))

        assert result.net_amount == Decimal("100.00")
        assert result.tax_amount == Decimal("14.00")
        assert result.gross_amount == Decimal("114.00")
        assert result.rate == EGYPT_STANDARD_RATE

    def test_calculate_tax_custom_rate(self):
        """Test calculating with custom rate."""
        result = self.service.calculate_tax(
            Decimal("100.00"),
            rate=EGYPT_ZERO_RATE,
        )

        assert result.net_amount == Decimal("100.00")
        assert result.tax_amount == Decimal("0.00")
        assert result.gross_amount == Decimal("100.00")

    def test_calculate_tax_inclusive(self):
        """Test extracting tax from inclusive amount."""
        result = self.service.calculate_tax(
            Decimal("114.00"),
            is_inclusive=True,
        )

        assert result.gross_amount == Decimal("114.00")
        assert result.net_amount == Decimal("100.00")
        assert result.tax_amount == Decimal("14.00")

    def test_calculate_tax_rounding(self):
        """Test tax calculation rounds correctly."""
        result = self.service.calculate_tax(Decimal("33.33"))

        # 33.33 * 0.14 = 4.6662 -> 4.67
        assert result.tax_amount == Decimal("4.67")
        assert result.gross_amount == Decimal("38.00")

    def test_calculate_line_tax(self):
        """Test calculating line item tax."""
        result = self.service.calculate_line_tax(
            unit_price=Decimal("100.00"),
            quantity=Decimal("3"),
            discount=Decimal("10.00"),
        )

        # sales_total = 300, net = 290, tax = 290 * 0.14 = 40.60
        assert result.net_amount == Decimal("290.00")
        assert result.tax_amount == Decimal("40.60")
        assert result.gross_amount == Decimal("330.60")
        assert result.breakdown["sales_total"] == Decimal("300.00")
        assert result.breakdown["discount"] == Decimal("10.00")

    def test_calculate_line_tax_no_discount(self):
        """Test line tax without discount."""
        result = self.service.calculate_line_tax(
            unit_price=Decimal("50.00"),
            quantity=Decimal("2"),
        )

        assert result.net_amount == Decimal("100.00")
        assert result.tax_amount == Decimal("14.00")
        assert result.gross_amount == Decimal("114.00")

    def test_validate_tax_id_valid(self):
        """Test validating correct Egyptian tax ID."""
        assert self.service.validate_tax_id("123456789") is True
        assert self.service.validate_tax_id("987654321") is True

    def test_validate_tax_id_invalid_length(self):
        """Test validating tax ID with wrong length."""
        assert self.service.validate_tax_id("12345678") is False  # 8 digits
        assert self.service.validate_tax_id("1234567890") is False  # 10 digits

    def test_validate_tax_id_non_numeric(self):
        """Test validating non-numeric tax ID."""
        assert self.service.validate_tax_id("12345678A") is False
        assert self.service.validate_tax_id("ABC456789") is False

    def test_validate_tax_id_with_formatting(self):
        """Test validating tax ID with dashes/spaces."""
        assert self.service.validate_tax_id("123-456-789") is True
        assert self.service.validate_tax_id("123 456 789") is True

    def test_format_tax_id(self):
        """Test formatting tax ID."""
        formatted = self.service.format_tax_id("123456789")
        assert formatted == "123-456-789"

    def test_calculate_invoice_totals(self):
        """Test calculating invoice totals from line items."""
        line_amounts = [
            (Decimal("100.00"), Decimal("14.00")),
            (Decimal("200.00"), Decimal("28.00")),
            (Decimal("50.00"), Decimal("7.00")),
        ]

        totals = self.service.calculate_invoice_totals(line_amounts)

        assert totals["subtotal"] == Decimal("350.00")
        assert totals["total_tax"] == Decimal("49.00")
        assert totals["total"] == Decimal("399.00")

    def test_calculate_invoice_totals_with_discount(self):
        """Test invoice totals with extra discount."""
        line_amounts = [
            (Decimal("100.00"), Decimal("14.00")),
        ]

        totals = self.service.calculate_invoice_totals(
            line_amounts,
            extra_discount=Decimal("10.00"),
        )

        # Discount reduces tax proportionally
        # 10% discount on net -> 10% reduction on tax
        assert totals["subtotal"] == Decimal("100.00")
        assert totals["extra_discount"] == Decimal("10.00")
        assert totals["total_tax"] == Decimal("12.60")  # 14 - 1.40
        assert totals["total"] == Decimal("102.60")  # 100 - 10 + 12.60
