"""Saudi Arabian tax service implementation.

Saudi Arabia (KSA) levies Value Added Tax (VAT) at a 15% standard rate
(raised from 5% on 1 July 2020), administered by ZATCA (the Zakat, Tax
and Customs Authority).

Key regulations:
- Standard VAT rate: 15%
- Registration threshold: SAR 375,000 annual taxable supplies
  (voluntary from SAR 187,500)
- E-invoicing (Fatoora) mandatory — Phase 2 (Integration) handled
  separately by the ZATCA invoice service (program Phase 4)
- Tax period: monthly (turnover ≥ SAR 40m) or quarterly
- VAT registration number (TRN): 15 digits, begins and ends with "3"

Mirrors EgyptianTaxService so both plug into ITaxService identically; the
factory in api/dependencies/tax.py selects by store.country.
"""

import re
from decimal import ROUND_HALF_UP, Decimal

from src.core.interfaces.services.tax_service import (
    ITaxService,
    TaxCalculation,
    TaxRate,
    TaxRateType,
)

# Saudi VAT rates
SAUDI_STANDARD_RATE = TaxRate(
    type=TaxRateType.STANDARD,
    rate=Decimal("15.00"),
    name="VAT",
    code="S",  # ZATCA UBL VAT category code for standard-rated supplies
)

SAUDI_ZERO_RATE = TaxRate(
    type=TaxRateType.ZERO,
    rate=Decimal("0.00"),
    name="Zero Rate",
    code="Z",  # ZATCA category code for zero-rated supplies
)

SAUDI_EXEMPT_RATE = TaxRate(
    type=TaxRateType.EXEMPT,
    rate=Decimal("0.00"),
    name="Exempt",
    code="E",  # ZATCA category code for exempt supplies
)

# Zero-rated supplies under KSA VAT (Article 33-35 of the Implementing
# Regulations): exports outside the GCC, international transport,
# qualifying medicines/medical goods, and investment-grade precious metals.
ZERO_RATED_CATEGORIES = {
    "exports",
    "international_transport",
    "medicines",
    "medical_equipment",
    "investment_metals",
}

# Exempt supplies: margin-based financial services, residential real-estate
# rental, and life insurance. (Real-estate SALES carry the separate 5% RETT,
# not VAT, and are out of scope for a storefront.)
EXEMPT_CATEGORIES = {
    "financial_services",
    "residential_rental",
    "life_insurance",
}


class SaudiTaxService(ITaxService):
    """Saudi VAT calculation service.

    Implements KSA tax regulations including:
    - 15% standard VAT rate
    - Zero-rated goods (exports, qualifying medicines, investment metals)
    - Exempt supplies (financial services, residential rental, life insurance)
    - 15-digit VAT registration number (TRN) validation
    """

    @property
    def country_code(self) -> str:
        """Get the country code."""
        return "SA"

    @property
    def standard_rate(self) -> TaxRate:
        """Get the standard VAT rate (15%)."""
        return SAUDI_STANDARD_RATE

    def get_rate_for_product(
        self,
        product_code: str,
        category: str | None = None,
    ) -> TaxRate:
        """Get the applicable VAT rate for a product.

        Args:
            product_code: Product / GS1 code
            category: Product category

        Returns:
            Applicable TaxRate (15%, 0%, or exempt)
        """
        if category:
            category_lower = category.lower()

            if category_lower in ZERO_RATED_CATEGORIES:
                return SAUDI_ZERO_RATE

            if category_lower in EXEMPT_CATEGORIES:
                return SAUDI_EXEMPT_RATE

        # Export product codes are zero-rated (simplified; production would
        # check against ZATCA's classification tables).
        if product_code.upper().startswith("EXPORT-"):
            return SAUDI_ZERO_RATE

        # Default to standard rate
        return SAUDI_STANDARD_RATE

    def calculate_tax(
        self,
        amount: Decimal,
        rate: TaxRate | None = None,
        is_inclusive: bool = False,
    ) -> TaxCalculation:
        """Calculate VAT for an amount.

        Args:
            amount: Net amount (or gross if is_inclusive=True)
            rate: Tax rate (uses 15% standard if None)
            is_inclusive: Whether amount already includes tax

        Returns:
            TaxCalculation with amounts

        Example:
            # Net amount 100, calculate 15% VAT
            result = service.calculate_tax(Decimal("100.00"))
            # result.net_amount = 100.00
            # result.tax_amount = 15.00
            # result.gross_amount = 115.00
        """
        tax_rate = rate or self.standard_rate
        rate_decimal = tax_rate.rate / Decimal("100")

        if is_inclusive:
            # Extract tax from gross: net = gross / (1 + rate)
            divisor = Decimal("1") + rate_decimal
            net_amount = (amount / divisor).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            tax_amount = (amount - net_amount).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            gross_amount = amount
        else:
            net_amount = amount
            tax_amount = (net_amount * rate_decimal).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            gross_amount = net_amount + tax_amount

        return TaxCalculation(
            net_amount=net_amount,
            tax_amount=tax_amount,
            gross_amount=gross_amount,
            rate=tax_rate,
        )

    def calculate_line_tax(
        self,
        unit_price: Decimal,
        quantity: Decimal,
        discount: Decimal = Decimal("0"),
        rate: TaxRate | None = None,
    ) -> TaxCalculation:
        """Calculate tax for an invoice line item.

        Args:
            unit_price: Price per unit (before tax)
            quantity: Number of units
            discount: Total discount for the line
            rate: Tax rate to apply

        Returns:
            TaxCalculation for the line
        """
        tax_rate = rate or self.standard_rate

        sales_total = (unit_price * quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        net_amount = (sales_total - discount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        rate_decimal = tax_rate.rate / Decimal("100")
        tax_amount = (net_amount * rate_decimal).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        gross_amount = net_amount + tax_amount

        return TaxCalculation(
            net_amount=net_amount,
            tax_amount=tax_amount,
            gross_amount=gross_amount,
            rate=tax_rate,
            breakdown={
                "sales_total": sales_total,
                "discount": discount,
            },
        )

    def validate_tax_id(self, tax_id: str) -> bool:
        """Validate a Saudi VAT registration number (TRN).

        The KSA TRN is 15 digits and, by ZATCA convention, both begins and
        ends with the digit "3". This is a format check only — it does not
        verify the number is live in ZATCA's registry.

        Args:
            tax_id: Tax ID to validate

        Returns:
            True if valid format
        """
        cleaned = tax_id.replace(" ", "").replace("-", "")

        if not re.match(r"^\d{15}$", cleaned):
            return False

        # ZATCA TRNs start and end with "3".
        return cleaned.startswith("3") and cleaned.endswith("3")

    def format_tax_id(self, tax_id: str) -> str:
        """Format a 15-digit TRN for display in 5-digit groups.

        Args:
            tax_id: Tax ID to format

        Returns:
            Formatted tax ID (XXXXX-XXXXX-XXXXX) or the input unchanged
        """
        cleaned = tax_id.replace(" ", "").replace("-", "")
        if len(cleaned) == 15:
            return f"{cleaned[:5]}-{cleaned[5:10]}-{cleaned[10:]}"
        return tax_id
