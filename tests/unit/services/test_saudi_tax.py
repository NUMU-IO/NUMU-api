"""Unit tests for Saudi tax service."""

from decimal import Decimal

from src.core.interfaces.services.tax_service import TaxRateType
from src.infrastructure.external_services.tax.saudi_tax_service import (
    SAUDI_EXEMPT_RATE,
    SAUDI_STANDARD_RATE,
    SAUDI_ZERO_RATE,
    SaudiTaxService,
)


class TestSaudiTaxService:
    """Tests for Saudi tax service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = SaudiTaxService()

    def test_country_code(self):
        """Test country code is SA."""
        assert self.service.country_code == "SA"

    def test_standard_rate(self):
        """Test standard VAT rate is 15%."""
        rate = self.service.standard_rate
        assert rate.rate == Decimal("15.00")
        assert rate.type == TaxRateType.STANDARD
        assert rate.code == "S"

    def test_get_rate_for_standard_product(self):
        """Standard product gets the 15% rate."""
        rate = self.service.get_rate_for_product("PROD-001")
        assert rate == SAUDI_STANDARD_RATE

    def test_get_rate_for_export(self):
        """Exports are zero-rated."""
        rate = self.service.get_rate_for_product("PROD-001", category="exports")
        assert rate == SAUDI_ZERO_RATE
        assert rate.rate == Decimal("0.00")

    def test_get_rate_for_export_code(self):
        """Export product codes are zero-rated."""
        rate = self.service.get_rate_for_product("EXPORT-123")
        assert rate == SAUDI_ZERO_RATE

    def test_get_rate_for_exempt_services(self):
        """Financial services are exempt."""
        rate = self.service.get_rate_for_product(
            "SVC-001", category="financial_services"
        )
        assert rate == SAUDI_EXEMPT_RATE

    def test_get_rate_for_residential_rental(self):
        """Residential rental is exempt."""
        rate = self.service.get_rate_for_product(
            "RENT-001", category="residential_rental"
        )
        assert rate == SAUDI_EXEMPT_RATE

    def test_calculate_tax_standard(self):
        """Calculating standard 15% VAT on a net amount."""
        result = self.service.calculate_tax(Decimal("100.00"))
        assert result.net_amount == Decimal("100.00")
        assert result.tax_amount == Decimal("15.00")
        assert result.gross_amount == Decimal("115.00")

    def test_calculate_tax_inclusive(self):
        """Extracting 15% VAT from a gross (tax-inclusive) amount."""
        result = self.service.calculate_tax(Decimal("115.00"), is_inclusive=True)
        assert result.net_amount == Decimal("100.00")
        assert result.tax_amount == Decimal("15.00")
        assert result.gross_amount == Decimal("115.00")

    def test_calculate_line_tax_with_discount(self):
        """Line tax on 3 units of 100 with a 10 discount → 15% of 290."""
        result = self.service.calculate_line_tax(
            unit_price=Decimal("100.00"),
            quantity=Decimal("3"),
            discount=Decimal("10.00"),
        )
        assert result.net_amount == Decimal("290.00")
        assert result.tax_amount == Decimal("43.50")
        assert result.gross_amount == Decimal("333.50")

    def test_validate_tax_id_valid(self):
        """A 15-digit TRN starting and ending with 3 is valid."""
        assert self.service.validate_tax_id("300000000000003") is True
        # tolerates spaces / dashes
        assert self.service.validate_tax_id("30000-00000-00003") is True

    def test_validate_tax_id_invalid(self):
        """Reject wrong length or wrong leading/trailing digit."""
        assert (
            self.service.validate_tax_id("123456789") is False
        )  # too short (EG style)
        assert self.service.validate_tax_id("100000000000001") is False  # not 3…3
        assert self.service.validate_tax_id("3000000000000031") is False  # 16 digits

    def test_format_tax_id(self):
        """Format a 15-digit TRN in 5-digit groups."""
        assert self.service.format_tax_id("300000000000003") == "30000-00000-00003"
