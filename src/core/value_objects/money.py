"""Money value object for handling monetary values."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Currency(str, Enum):
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


@dataclass(frozen=True)
class Money:
    """Money value object representing a monetary amount with currency."""

    amount: Decimal
    currency: Currency = Currency.USD

    def __post_init__(self) -> None:
        """Validate and normalize the amount."""
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        
        # Round to currency's decimal places
        decimals = CURRENCY_DECIMALS.get(self.currency, 2)
        quantize_str = "0." + "0" * decimals
        rounded = self.amount.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
        object.__setattr__(self, "amount", rounded)

    @classmethod
    def from_cents(cls, cents: int, currency: Currency = Currency.USD) -> "Money":
        """Create Money from cents (smallest currency unit)."""
        decimals = CURRENCY_DECIMALS.get(currency, 2)
        divisor = Decimal(10) ** decimals
        return cls(amount=Decimal(cents) / divisor, currency=currency)

    @property
    def cents(self) -> int:
        """Get amount in cents (smallest currency unit)."""
        decimals = CURRENCY_DECIMALS.get(self.currency, 2)
        multiplier = Decimal(10) ** decimals
        return int(self.amount * multiplier)

    def __add__(self, other: "Money") -> "Money":
        """Add two money values."""
        if self.currency != other.currency:
            raise ValueError("Cannot add money with different currencies")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        """Subtract two money values."""
        if self.currency != other.currency:
            raise ValueError("Cannot subtract money with different currencies")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, multiplier: int | float | Decimal) -> "Money":
        """Multiply money by a scalar."""
        return Money(amount=self.amount * Decimal(str(multiplier)), currency=self.currency)

    def __lt__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("Cannot compare money with different currencies")
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("Cannot compare money with different currencies")
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("Cannot compare money with different currencies")
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("Cannot compare money with different currencies")
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.currency.value} {self.amount}"

    def format(self, symbol: bool = True) -> str:
        """Format money for display."""
        symbols = {
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
        if symbol:
            return f"{symbols.get(self.currency, '')}{self.amount:,.2f}"
        return f"{self.amount:,.2f} {self.currency.value}"
