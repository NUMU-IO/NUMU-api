"""Money value object for handling monetary values."""

from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Currency(StrEnum):
    """Supported currencies."""

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    SAR = "SAR"  # Saudi Riyal
    AED = "AED"  # UAE Dirham
    KWD = "KWD"  # Kuwaiti Dinar
    BHD = "BHD"  # Bahraini Dinar
    QAR = "QAR"  # Qatari Riyal
    OMR = "OMR"  # Omani Rial
    EGP = "EGP"  # Egyptian Pound


# Currency decimal places (most are 2, some are 3)
CURRENCY_DECIMALS: dict[Currency, int] = {
    Currency.USD: 2,
    Currency.EUR: 2,
    Currency.GBP: 2,
    Currency.SAR: 2,
    Currency.AED: 2,
    Currency.KWD: 3,
    Currency.BHD: 3,
    Currency.QAR: 2,
    Currency.OMR: 3,
    Currency.EGP: 2,
}

# Currency symbols for formatting
CURRENCY_SYMBOLS: dict[Currency, str] = {
    Currency.USD: "$",
    Currency.EUR: "€",
    Currency.GBP: "£",
    Currency.SAR: "SAR ",
    Currency.AED: "AED ",
    Currency.KWD: "KWD ",
    Currency.BHD: "BHD ",
    Currency.QAR: "QAR ",
    Currency.OMR: "OMR ",
    Currency.EGP: "EGP ",
}


class Money(BaseModel):
    """Money value object representing a monetary amount with currency."""

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: Currency = Currency.USD

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_to_decimal(cls, v: Any) -> Decimal:
        """Coerce amount to Decimal."""
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))

    @model_validator(mode="after")
    def round_to_currency_precision(self) -> Self:
        """Round amount to currency's decimal places."""
        decimals = CURRENCY_DECIMALS.get(self.currency, 2)
        quantize_str = "0." + "0" * decimals
        rounded = self.amount.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
        # Use object.__setattr__ since frozen
        object.__setattr__(self, "amount", rounded)
        return self

    @classmethod
    def from_cents(cls, cents: int, currency: Currency = Currency.USD) -> "Money":
        """Create Money from cents (smallest currency unit)."""
        decimals = CURRENCY_DECIMALS.get(currency, 2)
        divisor = Decimal(10) ** decimals
        return cls(amount=Decimal(cents) / divisor, currency=currency)

    @classmethod
    def zero(cls, currency: Currency = Currency.USD) -> "Money":
        """Create a zero Money value."""
        return cls(amount=Decimal("0"), currency=currency)

    @property
    def cents(self) -> int:
        """Get amount in cents (smallest currency unit)."""
        decimals = CURRENCY_DECIMALS.get(self.currency, 2)
        multiplier = Decimal(10) ** decimals
        return int(self.amount * multiplier)

    @property
    def is_zero(self) -> bool:
        """Check if amount is zero."""
        return self.amount == Decimal("0")

    @property
    def is_positive(self) -> bool:
        """Check if amount is positive."""
        return self.amount > Decimal("0")

    @property
    def is_negative(self) -> bool:
        """Check if amount is negative."""
        return self.amount < Decimal("0")

    def __add__(self, other: "Money") -> "Money":
        """Add two money values."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot add Money and {type(other).__name__}")
        if self.currency != other.currency:
            raise ValueError("Cannot add money with different currencies")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        """Subtract two money values."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot subtract Money and {type(other).__name__}")
        if self.currency != other.currency:
            raise ValueError("Cannot subtract money with different currencies")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, multiplier: int | float | Decimal) -> "Money":
        """Multiply money by a scalar."""
        return Money(
            amount=self.amount * Decimal(str(multiplier)), currency=self.currency
        )

    def __rmul__(self, multiplier: int | float | Decimal) -> "Money":
        """Right multiply money by a scalar."""
        return self.__mul__(multiplier)

    def __neg__(self) -> "Money":
        """Negate the money value."""
        return Money(amount=-self.amount, currency=self.currency)

    def __abs__(self) -> "Money":
        """Get absolute value of money."""
        return Money(amount=abs(self.amount), currency=self.currency)

    def __lt__(self, other: "Money") -> bool:
        """Less than comparison."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot compare Money and {type(other).__name__}")
        if self.currency != other.currency:
            raise ValueError("Cannot compare money with different currencies")
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        """Less than or equal comparison."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot compare Money and {type(other).__name__}")
        if self.currency != other.currency:
            raise ValueError("Cannot compare money with different currencies")
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        """Greater than comparison."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot compare Money and {type(other).__name__}")
        if self.currency != other.currency:
            raise ValueError("Cannot compare money with different currencies")
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        """Greater than or equal comparison."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot compare Money and {type(other).__name__}")
        if self.currency != other.currency:
            raise ValueError("Cannot compare money with different currencies")
        return self.amount >= other.amount

    def __hash__(self) -> int:
        """Hash for use in sets and dict keys."""
        return hash((self.amount, self.currency))

    def __str__(self) -> str:
        """String representation."""
        return f"{self.currency.value} {self.amount}"

    def __repr__(self) -> str:
        """Detailed representation."""
        return f"Money(amount={self.amount!r}, currency={self.currency!r})"

    def format(self, symbol: bool = True, locale_format: bool = True) -> str:
        """Format money for display.

        Args:
            symbol: If True, use currency symbol (e.g., $). If False, use currency code.
            locale_format: If True, use thousand separators.

        Returns:
            Formatted money string.
        """
        if locale_format:
            formatted_amount = f"{self.amount:,.2f}"
        else:
            formatted_amount = f"{self.amount:.2f}"

        if symbol:
            currency_symbol = CURRENCY_SYMBOLS.get(self.currency, "")
            return f"{currency_symbol}{formatted_amount}"
        return f"{formatted_amount} {self.currency.value}"

    def allocate(self, ratios: list[int]) -> list["Money"]:
        """Allocate money according to ratios without losing cents.

        This is useful for splitting bills or distributing payments.
        The remainder cents are distributed to the first allocations.

        Args:
            ratios: List of integer ratios (e.g., [1, 1, 1] for equal three-way split)

        Returns:
            List of Money objects that sum exactly to self.
        """
        if not ratios:
            raise ValueError("Ratios list cannot be empty")
        if any(r < 0 for r in ratios):
            raise ValueError("Ratios cannot be negative")

        total_ratio = sum(ratios)
        if total_ratio == 0:
            raise ValueError("Sum of ratios cannot be zero")

        total_cents = self.cents
        results = []
        allocated_cents = 0

        for i, ratio in enumerate(ratios):
            if i == len(ratios) - 1:
                # Last allocation gets the remainder
                share_cents = total_cents - allocated_cents
            else:
                share_cents = (total_cents * ratio) // total_ratio
                allocated_cents += share_cents

            results.append(Money.from_cents(share_cents, self.currency))

        return results
