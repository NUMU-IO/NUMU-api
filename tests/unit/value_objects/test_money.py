"""Unit tests for Money value object."""

from decimal import Decimal

import pytest

from src.core.value_objects.money import Currency, Money


class TestMoney:
    """Tests for Money value object."""

    def test_create_money_with_decimal(self):
        """Test creating Money with Decimal amount."""
        money = Money(amount=Decimal("100.00"), currency=Currency.USD)

        assert money.amount == Decimal("100.00")
        assert money.currency == Currency.USD

    def test_create_money_with_string(self):
        """Test creating Money with string amount."""
        money = Money(amount="99.99", currency=Currency.EGP)

        assert money.amount == Decimal("99.99")
        assert money.currency == Currency.EGP

    def test_create_money_with_int(self):
        """Test creating Money with int amount."""
        money = Money(amount=50, currency=Currency.EUR)

        assert money.amount == Decimal("50.00")

    def test_money_default_currency(self):
        """Test Money defaults to USD."""
        money = Money(amount=Decimal("10.00"))

        assert money.currency == Currency.USD

    def test_money_rounds_to_currency_precision(self):
        """Test Money rounds to currency's decimal places."""
        # USD has 2 decimal places
        money_usd = Money(amount=Decimal("10.999"), currency=Currency.USD)
        assert money_usd.amount == Decimal("11.00")

        # KWD has 3 decimal places
        money_kwd = Money(amount=Decimal("10.9999"), currency=Currency.KWD)
        assert money_kwd.amount == Decimal("11.000")

    def test_money_from_cents(self):
        """Test creating Money from cents."""
        money = Money.from_cents(1000, Currency.USD)

        assert money.amount == Decimal("10.00")
        assert money.cents == 1000

    def test_money_from_cents_kwd(self):
        """Test creating Money from cents with 3 decimal currency."""
        money = Money.from_cents(1000, Currency.KWD)

        assert money.amount == Decimal("1.000")
        assert money.cents == 1000

    def test_money_zero(self):
        """Test creating zero Money."""
        money = Money.zero(Currency.EGP)

        assert money.amount == Decimal("0.00")
        assert money.is_zero is True

    def test_money_cents_property(self):
        """Test cents property."""
        money = Money(amount=Decimal("25.50"), currency=Currency.USD)

        assert money.cents == 2550

    def test_money_is_positive(self):
        """Test is_positive property."""
        positive = Money(amount=Decimal("10.00"))
        zero = Money.zero()
        negative = Money(amount=Decimal("-10.00"))

        assert positive.is_positive is True
        assert zero.is_positive is False
        assert negative.is_positive is False

    def test_money_is_negative(self):
        """Test is_negative property."""
        positive = Money(amount=Decimal("10.00"))
        zero = Money.zero()
        negative = Money(amount=Decimal("-10.00"))

        assert positive.is_negative is False
        assert zero.is_negative is False
        assert negative.is_negative is True

    def test_money_addition(self):
        """Test adding Money values."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("5.50"), currency=Currency.USD)

        result = money1 + money2

        assert result.amount == Decimal("15.50")
        assert result.currency == Currency.USD

    def test_money_addition_different_currencies_fails(self):
        """Test adding Money with different currencies fails."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("5.50"), currency=Currency.EGP)

        with pytest.raises(ValueError, match="different currencies"):
            money1 + money2

    def test_money_addition_non_money_fails(self):
        """Test adding Money with non-Money fails."""
        money = Money(amount=Decimal("10.00"))

        with pytest.raises(TypeError):
            money + 5

    def test_money_subtraction(self):
        """Test subtracting Money values."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("3.50"), currency=Currency.USD)

        result = money1 - money2

        assert result.amount == Decimal("6.50")

    def test_money_subtraction_different_currencies_fails(self):
        """Test subtracting Money with different currencies fails."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("5.50"), currency=Currency.EUR)

        with pytest.raises(ValueError, match="different currencies"):
            money1 - money2

    def test_money_multiplication(self):
        """Test multiplying Money by scalar."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)

        result = money * 3

        assert result.amount == Decimal("30.00")

    def test_money_multiplication_decimal(self):
        """Test multiplying Money by decimal scalar."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)

        result = money * Decimal("1.5")

        assert result.amount == Decimal("15.00")

    def test_money_right_multiplication(self):
        """Test right multiplication."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)

        result = 2 * money

        assert result.amount == Decimal("20.00")

    def test_money_negation(self):
        """Test negating Money."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)

        result = -money

        assert result.amount == Decimal("-10.00")

    def test_money_absolute(self):
        """Test absolute value of Money."""
        money = Money(amount=Decimal("-10.00"), currency=Currency.USD)

        result = abs(money)

        assert result.amount == Decimal("10.00")

    def test_money_comparison_less_than(self):
        """Test less than comparison."""
        money1 = Money(amount=Decimal("5.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("10.00"), currency=Currency.USD)

        assert money1 < money2
        assert not money2 < money1

    def test_money_comparison_less_equal(self):
        """Test less than or equal comparison."""
        money1 = Money(amount=Decimal("5.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("5.00"), currency=Currency.USD)
        money3 = Money(amount=Decimal("10.00"), currency=Currency.USD)

        assert money1 <= money2
        assert money1 <= money3

    def test_money_comparison_greater_than(self):
        """Test greater than comparison."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("5.00"), currency=Currency.USD)

        assert money1 > money2

    def test_money_comparison_greater_equal(self):
        """Test greater than or equal comparison."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("10.00"), currency=Currency.USD)

        assert money1 >= money2

    def test_money_comparison_different_currencies_fails(self):
        """Test comparison with different currencies fails."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("10.00"), currency=Currency.EGP)

        with pytest.raises(ValueError, match="different currencies"):
            money1 < money2

    def test_money_hash(self):
        """Test Money can be used in sets and dicts."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money3 = Money(amount=Decimal("10.00"), currency=Currency.EGP)

        assert hash(money1) == hash(money2)
        assert hash(money1) != hash(money3)

        # Can be used in set
        money_set = {money1, money2, money3}
        assert len(money_set) == 2

    def test_money_str(self):
        """Test string representation."""
        money = Money(amount=Decimal("100.50"), currency=Currency.EGP)

        assert str(money) == "EGP 100.50"

    def test_money_repr(self):
        """Test detailed representation."""
        money = Money(amount=Decimal("100.50"), currency=Currency.USD)

        repr_str = repr(money)
        assert "Money" in repr_str
        assert "100.50" in repr_str
        assert "USD" in repr_str

    def test_money_format_with_symbol(self):
        """Test formatting with currency symbol."""
        money = Money(amount=Decimal("1234.56"), currency=Currency.USD)

        formatted = money.format(symbol=True, locale_format=True)

        assert "$" in formatted
        assert "1,234.56" in formatted

    def test_money_format_without_symbol(self):
        """Test formatting without currency symbol."""
        money = Money(amount=Decimal("1234.56"), currency=Currency.USD)

        formatted = money.format(symbol=False)

        assert "USD" in formatted
        assert "$" not in formatted

    def test_money_format_without_locale(self):
        """Test formatting without locale (no thousand separator)."""
        money = Money(amount=Decimal("1234.56"), currency=Currency.USD)

        formatted = money.format(symbol=True, locale_format=False)

        assert "1234.56" in formatted

    def test_money_allocate_equal(self):
        """Test allocating money equally."""
        money = Money(amount=Decimal("100.00"), currency=Currency.USD)

        parts = money.allocate([1, 1, 1])

        assert len(parts) == 3
        assert sum(p.cents for p in parts) == money.cents

    def test_money_allocate_unequal(self):
        """Test allocating money unequally."""
        money = Money(amount=Decimal("100.00"), currency=Currency.USD)

        parts = money.allocate([2, 1])  # 2:1 ratio

        assert len(parts) == 2
        assert sum(p.cents for p in parts) == money.cents
        # First part should be roughly 2/3
        assert parts[0].cents > parts[1].cents

    def test_money_allocate_handles_remainder(self):
        """Test allocating handles remainder correctly."""
        # $100 split 3 ways = $33.33 + $33.33 + $33.34 = $100
        money = Money(amount=Decimal("100.00"), currency=Currency.USD)

        parts = money.allocate([1, 1, 1])

        total_cents = sum(p.cents for p in parts)
        assert total_cents == 10000  # Exact original amount

    def test_money_allocate_empty_ratios_fails(self):
        """Test allocating with empty ratios fails."""
        money = Money(amount=Decimal("100.00"))

        with pytest.raises(ValueError, match="empty"):
            money.allocate([])

    def test_money_allocate_negative_ratios_fails(self):
        """Test allocating with negative ratios fails."""
        money = Money(amount=Decimal("100.00"))

        with pytest.raises(ValueError, match="negative"):
            money.allocate([1, -1])

    def test_money_allocate_zero_sum_fails(self):
        """Test allocating with zero sum ratios fails."""
        money = Money(amount=Decimal("100.00"))

        with pytest.raises(ValueError, match="zero"):
            money.allocate([0, 0])


class TestCurrency:
    """Tests for Currency enum."""

    def test_currency_values(self):
        """Test currency values exist."""
        assert Currency.USD == "USD"
        assert Currency.EGP == "EGP"
        assert Currency.EUR == "EUR"
        assert Currency.SAR == "SAR"

    def test_currency_from_string(self):
        """Test creating currency from string."""
        currency = Currency("USD")
        assert currency == Currency.USD

    def test_invalid_currency_fails(self):
        """Test invalid currency string fails."""
        with pytest.raises(ValueError):
            Currency("INVALID")
