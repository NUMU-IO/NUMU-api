"""Tax service interface for tax calculations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class TaxRateType(StrEnum):
    """Types of tax rates."""

    STANDARD = "standard"  # Standard VAT rate
    REDUCED = "reduced"  # Reduced rate
    ZERO = "zero"  # Zero-rated
    EXEMPT = "exempt"  # Tax exempt


@dataclass
class TaxRate:
    """Tax rate information."""

    type: TaxRateType
    rate: Decimal  # Percentage (e.g., 14.00 for 14%)
    name: str
    code: str  # Tax authority code


@dataclass
class TaxCalculation:
    """Result of tax calculation."""

    net_amount: Decimal  # Amount before tax
    tax_amount: Decimal  # Tax amount
    gross_amount: Decimal  # Amount after tax
    rate: TaxRate
    breakdown: dict[str, Decimal] | None = None  # Detailed breakdown


class ITaxService(ABC):
    """Tax calculation service interface."""

    @property
    @abstractmethod
    def country_code(self) -> str:
        """Get the country code this service handles."""
        ...

    @property
    @abstractmethod
    def standard_rate(self) -> TaxRate:
        """Get the standard tax rate."""
        ...

    @abstractmethod
    def get_rate_for_product(
        self,
        product_code: str,
        category: str | None = None,
    ) -> TaxRate:
        """Get the applicable tax rate for a product.

        Args:
            product_code: Product code (EGS, GS1, etc.)
            category: Product category

        Returns:
            Applicable TaxRate
        """
        ...

    @abstractmethod
    def calculate_tax(
        self,
        amount: Decimal,
        rate: TaxRate | None = None,
        is_inclusive: bool = False,
    ) -> TaxCalculation:
        """Calculate tax for an amount.

        Args:
            amount: Net amount (before tax) or gross amount (if inclusive)
            rate: Tax rate to apply (uses standard if None)
            is_inclusive: If True, amount includes tax

        Returns:
            TaxCalculation with net, tax, and gross amounts
        """
        ...

    @abstractmethod
    def calculate_line_tax(
        self,
        unit_price: Decimal,
        quantity: Decimal,
        discount: Decimal = Decimal("0"),
        rate: TaxRate | None = None,
    ) -> TaxCalculation:
        """Calculate tax for a line item.

        Args:
            unit_price: Price per unit
            quantity: Number of units
            discount: Discount amount
            rate: Tax rate to apply

        Returns:
            TaxCalculation for the line
        """
        ...

    @abstractmethod
    def validate_tax_id(self, tax_id: str) -> bool:
        """Validate a tax identification number.

        Args:
            tax_id: Tax ID to validate

        Returns:
            True if valid format
        """
        ...
