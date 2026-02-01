"""Egyptian tax service implementation.

Egypt uses Value Added Tax (VAT) at 14% standard rate.
Some goods and services have reduced rates or exemptions.

Key regulations:
- Standard VAT rate: 14%
- Registration threshold: EGP 500,000 annual turnover
- E-invoicing mandatory for B2B transactions
- Tax period: Monthly filing
"""

import re
from decimal import ROUND_HALF_UP, Decimal

from src.core.interfaces.services.tax_service import (
    ITaxService,
    TaxCalculation,
    TaxRate,
    TaxRateType,
)


# Egyptian VAT rates
EGYPT_STANDARD_RATE = TaxRate(
    type=TaxRateType.STANDARD,
    rate=Decimal("14.00"),
    name="VAT",
    code="T1",
)

EGYPT_ZERO_RATE = TaxRate(
    type=TaxRateType.ZERO,
    rate=Decimal("0.00"),
    name="Zero Rate",
    code="T1",
)

EGYPT_EXEMPT_RATE = TaxRate(
    type=TaxRateType.EXEMPT,
    rate=Decimal("0.00"),
    name="Exempt",
    code="T1",
)

# Categories with special rates
ZERO_RATED_CATEGORIES = {
    "exports",  # Export goods
    "international_transport",
    "basic_food",  # Some basic foodstuffs
}

EXEMPT_CATEGORIES = {
    "financial_services",
    "insurance",
    "healthcare",
    "education",
    "real_estate_rental",
}


class EgyptianTaxService(ITaxService):
    """Egyptian VAT calculation service.

    Implements Egyptian tax regulations including:
    - 14% standard VAT rate
    - Zero-rated goods (exports, certain food items)
    - Exempt services (financial, healthcare, education)
    - Tax ID (RN) validation
    """

    @property
    def country_code(self) -> str:
        """Get the country code."""
        return "EG"

    @property
    def standard_rate(self) -> TaxRate:
        """Get the standard VAT rate (14%)."""
        return EGYPT_STANDARD_RATE

    def get_rate_for_product(
        self,
        product_code: str,
        category: str | None = None,
    ) -> TaxRate:
        """Get the applicable VAT rate for a product.

        Args:
            product_code: EGS or GS1 product code
            category: Product category

        Returns:
            Applicable TaxRate (14%, 0%, or exempt)
        """
        if category:
            category_lower = category.lower()

            if category_lower in ZERO_RATED_CATEGORIES:
                return EGYPT_ZERO_RATE

            if category_lower in EXEMPT_CATEGORIES:
                return EGYPT_EXEMPT_RATE

        # Check product code patterns (simplified)
        # In production, this would check against ETA's code tables
        if product_code.startswith("EGS-EXPORT-"):
            return EGYPT_ZERO_RATE

        # Default to standard rate
        return EGYPT_STANDARD_RATE

    def calculate_tax(
        self,
        amount: Decimal,
        rate: TaxRate | None = None,
        is_inclusive: bool = False,
    ) -> TaxCalculation:
        """Calculate VAT for an amount.

        Args:
            amount: Net amount (or gross if is_inclusive=True)
            rate: Tax rate (uses 14% standard if None)
            is_inclusive: Whether amount already includes tax

        Returns:
            TaxCalculation with amounts

        Example:
            # Net amount 100, calculate 14% VAT
            result = service.calculate_tax(Decimal("100.00"))
            # result.net_amount = 100.00
            # result.tax_amount = 14.00
            # result.gross_amount = 114.00

            # Gross amount 114 includes VAT
            result = service.calculate_tax(Decimal("114.00"), is_inclusive=True)
            # result.net_amount = 100.00
            # result.tax_amount = 14.00
            # result.gross_amount = 114.00
        """
        tax_rate = rate or self.standard_rate
        rate_decimal = tax_rate.rate / Decimal("100")

        if is_inclusive:
            # Extract tax from gross amount
            # gross = net * (1 + rate)
            # net = gross / (1 + rate)
            divisor = Decimal("1") + rate_decimal
            net_amount = (amount / divisor).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            tax_amount = (amount - net_amount).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            gross_amount = amount
        else:
            # Calculate tax on net amount
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

        Example:
            # 3 items at 100 each, 10 discount, 14% VAT
            result = service.calculate_line_tax(
                unit_price=Decimal("100.00"),
                quantity=Decimal("3"),
                discount=Decimal("10.00"),
            )
            # sales_total = 300.00
            # net_amount = 290.00 (300 - 10)
            # tax_amount = 40.60 (290 * 0.14)
            # gross_amount = 330.60
        """
        tax_rate = rate or self.standard_rate

        # Calculate line amounts
        sales_total = (unit_price * quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        net_amount = (sales_total - discount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Calculate tax
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
        """Validate Egyptian Tax Registration Number (RN).

        Egyptian tax IDs are 9 digits.

        Args:
            tax_id: Tax ID to validate

        Returns:
            True if valid format
        """
        # Remove any spaces or dashes
        cleaned = tax_id.replace(" ", "").replace("-", "")

        # Egyptian RN is 9 digits
        if not re.match(r"^\d{9}$", cleaned):
            return False

        return True

    def format_tax_id(self, tax_id: str) -> str:
        """Format tax ID for display.

        Args:
            tax_id: Tax ID to format

        Returns:
            Formatted tax ID (XXX-XXX-XXX)
        """
        cleaned = tax_id.replace(" ", "").replace("-", "")
        if len(cleaned) == 9:
            return f"{cleaned[:3]}-{cleaned[3:6]}-{cleaned[6:]}"
        return tax_id

    def calculate_invoice_totals(
        self,
        line_amounts: list[tuple[Decimal, Decimal]],
        extra_discount: Decimal = Decimal("0"),
    ) -> dict[str, Decimal]:
        """Calculate invoice totals from line items.

        Args:
            line_amounts: List of (net_amount, tax_amount) tuples
            extra_discount: Additional invoice-level discount

        Returns:
            Dictionary with total calculations
        """
        total_net = sum(net for net, _ in line_amounts)
        total_tax = sum(tax for _, tax in line_amounts)

        # Apply extra discount proportionally (reduces tax too)
        if extra_discount > 0:
            discount_ratio = extra_discount / total_net
            tax_reduction = (total_tax * discount_ratio).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_tax -= tax_reduction

        total_gross = total_net - extra_discount + total_tax

        return {
            "subtotal": total_net.quantize(Decimal("0.01")),
            "total_tax": total_tax.quantize(Decimal("0.01")),
            "extra_discount": extra_discount.quantize(Decimal("0.01")),
            "total": total_gross.quantize(Decimal("0.01")),
        }
