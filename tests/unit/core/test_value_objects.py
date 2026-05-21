"""Unit tests for Pydantic value objects."""

from decimal import Decimal

import pytest

from src.core.value_objects.address import Address
from src.core.value_objects.email import Email
from src.core.value_objects.money import Currency, Money
from src.core.value_objects.phone import PhoneNumber


class TestEmailValueObject:
    """Tests for the Email value object."""

    def test_valid_email(self):
        """Test creating email with valid value."""
        email = Email(value="test@example.com")
        assert email.value == "test@example.com"

    def test_email_normalization(self):
        """Test email is normalized to lowercase."""
        email = Email(value="TEST@EXAMPLE.COM")
        assert email.value == "test@example.com"

    def test_email_strips_whitespace(self):
        """Test email strips leading/trailing whitespace."""
        email = Email(value="  test@example.com  ")
        assert email.value == "test@example.com"

    def test_invalid_email_raises_error(self):
        """Test invalid email raises ValueError."""
        with pytest.raises(ValueError):
            Email(value="invalid-email")

    def test_empty_email_raises_error(self):
        """Test empty email raises ValueError."""
        with pytest.raises(ValueError):
            Email(value="")

    def test_email_without_domain_raises_error(self):
        """Test email without domain raises ValueError."""
        with pytest.raises(ValueError):
            Email(value="test@")

    def test_email_without_local_part_raises_error(self):
        """Test email without local part raises ValueError."""
        with pytest.raises(ValueError):
            Email(value="@example.com")

    def test_email_equality(self):
        """Test email equality based on normalized value."""
        email1 = Email(value="test@example.com")
        email2 = Email(value="TEST@EXAMPLE.COM")
        assert email1 == email2

    def test_email_hash(self):
        """Test email is hashable."""
        email = Email(value="test@example.com")
        email_set = {email}
        assert email in email_set

    def test_email_immutability(self):
        """Test email is immutable (frozen)."""
        email = Email(value="test@example.com")
        with pytest.raises(Exception):  # ValidationError for frozen model
            email.value = "other@example.com"

    def test_email_str_representation(self):
        """Test email string representation."""
        email = Email(value="test@example.com")
        assert str(email) == "test@example.com"


class TestPhoneNumberValueObject:
    """Tests for the PhoneNumber value object."""

    def test_valid_phone_egypt(self):
        """Test creating Egyptian phone number."""
        phone = PhoneNumber(value="01234567890", country_code="EG")
        assert "1234567890" in phone.value

    def test_valid_phone_international(self):
        """Test creating phone with international format."""
        phone = PhoneNumber(value="+201234567890", country_code="EG")
        assert phone.value == "+201234567890"

    def test_phone_normalization(self):
        """Test phone number normalization removes spaces and dashes."""
        phone = PhoneNumber(value="+20 123 456 7890", country_code="EG")
        assert " " not in phone.value
        assert "-" not in phone.value

    def test_invalid_phone_is_not_valid(self):
        """Test invalid phone number has is_valid=False."""
        phone = PhoneNumber(value="abc", country_code="EG")
        assert phone.is_valid is False

    def test_too_short_phone_is_not_valid(self):
        """Test too short phone number has is_valid=False."""
        phone = PhoneNumber(value="123", country_code="EG")
        assert phone.is_valid is False

    def test_phone_equality(self):
        """Test phone equality based on normalized value."""
        phone1 = PhoneNumber(value="+201234567890", country_code="EG")
        phone2 = PhoneNumber(value="+201234567890", country_code="EG")
        assert phone1 == phone2

    def test_phone_hash(self):
        """Test phone is hashable."""
        phone = PhoneNumber(value="+201234567890", country_code="EG")
        phone_set = {phone}
        assert phone in phone_set

    def test_phone_immutability(self):
        """Test phone is immutable (frozen)."""
        phone = PhoneNumber(value="+201234567890", country_code="EG")
        with pytest.raises(Exception):
            phone.value = "+20987654321"


class TestMoneyValueObject:
    """Tests for the Money value object."""

    def test_money_creation_with_decimal(self):
        """Test creating money with Decimal amount."""
        money = Money(amount=Decimal("19.99"), currency=Currency.USD)
        assert money.amount == Decimal("19.99")
        assert money.currency == Currency.USD

    def test_money_creation_with_float(self):
        """Test creating money with float (coerced to Decimal)."""
        money = Money(amount=19.99, currency=Currency.USD)
        assert money.amount == Decimal("19.99")

    def test_money_creation_with_int(self):
        """Test creating money with int (coerced to Decimal)."""
        money = Money(amount=100, currency=Currency.USD)
        assert money.amount == Decimal("100.00")

    def test_money_from_cents(self):
        """Test creating money from cents."""
        money = Money.from_cents(1999, Currency.USD)
        assert money.amount == Decimal("19.99")

    def test_money_cents_property(self):
        """Test cents property returns correct value."""
        money = Money(amount=Decimal("19.99"), currency=Currency.USD)
        assert money.cents == 1999

    def test_money_zero(self):
        """Test creating zero money."""
        money = Money.zero(Currency.EUR)
        assert money.amount == Decimal("0.00")
        assert money.currency == Currency.EUR
        assert money.is_zero

    def test_money_is_positive(self):
        """Test is_positive property."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)
        assert money.is_positive
        assert not money.is_zero

    def test_money_addition(self):
        """Test adding money values."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("5.50"), currency=Currency.USD)
        result = money1 + money2
        assert result.amount == Decimal("15.50")
        assert result.currency == Currency.USD

    def test_money_addition_different_currency_raises_error(self):
        """Test adding money with different currencies raises error."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("5.00"), currency=Currency.EUR)
        with pytest.raises(ValueError, match="different currencies"):
            _ = money1 + money2

    def test_money_subtraction(self):
        """Test subtracting money values."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("3.50"), currency=Currency.USD)
        result = money1 - money2
        assert result.amount == Decimal("6.50")

    def test_money_multiplication(self):
        """Test multiplying money by scalar."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)
        result = money * 3
        assert result.amount == Decimal("30.00")

    def test_money_multiplication_by_float(self):
        """Test multiplying money by float."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)
        result = money * 1.5
        assert result.amount == Decimal("15.00")

    def test_money_right_multiplication(self):
        """Test right multiplication."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)
        result = 2 * money
        assert result.amount == Decimal("20.00")

    def test_money_negation(self):
        """Test negating money value."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)
        result = -money
        assert result.amount == Decimal("-10.00")

    def test_money_absolute(self):
        """Test absolute value of money."""
        money = Money(amount=Decimal("-10.00"), currency=Currency.USD)
        result = abs(money)
        assert result.amount == Decimal("10.00")

    def test_money_comparison_less_than(self):
        """Test less than comparison."""
        money1 = Money(amount=Decimal("5.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        assert money1 < money2
        assert not money2 < money1

    def test_money_comparison_greater_than(self):
        """Test greater than comparison."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("5.00"), currency=Currency.USD)
        assert money1 > money2

    def test_money_comparison_less_equal(self):
        """Test less than or equal comparison."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        assert money1 <= money2
        assert money2 <= money1

    def test_money_comparison_greater_equal(self):
        """Test greater than or equal comparison."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        assert money1 >= money2

    def test_money_comparison_different_currency_raises_error(self):
        """Test comparing money with different currencies raises error."""
        money1 = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money2 = Money(amount=Decimal("10.00"), currency=Currency.EUR)
        with pytest.raises(ValueError, match="different currencies"):
            _ = money1 < money2

    def test_money_hash(self):
        """Test money is hashable."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)
        money_set = {money}
        assert money in money_set

    def test_money_immutability(self):
        """Test money is immutable (frozen)."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)
        with pytest.raises(Exception):
            money.amount = Decimal("20.00")

    def test_money_format_with_symbol(self):
        """Test formatting money with currency symbol."""
        money = Money(amount=Decimal("1234.56"), currency=Currency.USD)
        formatted = money.format(symbol=True)
        assert "$" in formatted
        assert "1,234.56" in formatted

    def test_money_format_without_symbol(self):
        """Test formatting money without currency symbol."""
        money = Money(amount=Decimal("1234.56"), currency=Currency.USD)
        formatted = money.format(symbol=False)
        assert "USD" in formatted
        assert "1,234.56" in formatted

    def test_money_allocate_equal_split(self):
        """Test allocating money equally."""
        money = Money(amount=Decimal("100.00"), currency=Currency.USD)
        allocations = money.allocate([1, 1, 1])
        assert len(allocations) == 3
        total = sum(a.cents for a in allocations)
        assert total == money.cents

    def test_money_allocate_unequal_split(self):
        """Test allocating money with unequal ratios."""
        money = Money(amount=Decimal("100.00"), currency=Currency.USD)
        allocations = money.allocate([50, 30, 20])
        assert allocations[0].amount == Decimal("50.00")
        assert allocations[1].amount == Decimal("30.00")
        assert allocations[2].amount == Decimal("20.00")

    def test_money_allocate_handles_remainder(self):
        """Test allocation handles remainder cents correctly."""
        money = Money(amount=Decimal("10.00"), currency=Currency.USD)
        allocations = money.allocate([1, 1, 1])
        total = sum(a.cents for a in allocations)
        assert total == 1000  # No cents lost

    def test_money_allocate_empty_raises_error(self):
        """Test allocating with empty ratios raises error."""
        money = Money(amount=Decimal("100.00"), currency=Currency.USD)
        with pytest.raises(ValueError, match="empty"):
            money.allocate([])

    def test_money_allocate_negative_ratio_raises_error(self):
        """Test allocating with negative ratio raises error."""
        money = Money(amount=Decimal("100.00"), currency=Currency.USD)
        with pytest.raises(ValueError, match="negative"):
            money.allocate([1, -1, 1])

    def test_money_three_decimal_currency(self):
        """Test money with 3 decimal places (KWD)."""
        money = Money(amount=Decimal("10.123"), currency=Currency.KWD)
        assert money.amount == Decimal("10.123")
        assert money.cents == 10123  # 3 decimal places


class TestAddressValueObject:
    """Tests for the Address value object."""

    def test_address_creation(self):
        """Test creating address with valid values."""
        address = Address(
            address_line1="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        assert address.address_line1 == "123 Main St"
        assert address.city == "New York"
        assert address.state == "NY"
        assert address.postal_code == "10001"
        assert address.country == "US"

    def test_address_minimal_creation(self):
        """Test creating address with only required fields."""
        address = Address(
            address_line1="123 Main St",
            city="Cairo",
            country="EG",
        )
        assert address.address_line1 == "123 Main St"
        assert address.city == "Cairo"
        assert address.country == "EG"
        assert address.state is None
        assert address.postal_code is None

    def test_address_formatted_single_line(self):
        """Test formatted single line address."""
        address = Address(
            address_line1="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        formatted = address.formatted_single_line
        assert "123 Main St" in formatted
        assert "New York" in formatted
        assert "NY" in formatted
        assert "10001" in formatted
        assert "US" in formatted

    def test_address_formatted_multi_line(self):
        """Test formatted multi-line address."""
        address = Address(
            address_line1="123 Main St",
            address_line2="Apt 4B",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        formatted = address.formatted_multi_line
        assert "123 Main St" in formatted
        assert "Apt 4B" in formatted
        assert "\n" in formatted

    def test_address_equality(self):
        """Test address equality."""
        addr1 = Address(
            address_line1="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        addr2 = Address(
            address_line1="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        assert addr1 == addr2

    def test_address_inequality(self):
        """Test address inequality."""
        addr1 = Address(address_line1="123 Main St", city="New York", country="US")
        addr2 = Address(address_line1="456 Oak Ave", city="New York", country="US")
        assert addr1 != addr2

    def test_address_hash(self):
        """Test address is hashable."""
        address = Address(address_line1="123 Main St", city="Cairo", country="EG")
        address_set = {address}
        assert address in address_set

    def test_address_immutability(self):
        """Test address is immutable (frozen)."""
        address = Address(address_line1="123 Main St", city="Cairo", country="EG")
        with pytest.raises(Exception):
            address.address_line1 = "456 Oak Ave"

    def test_address_to_dict(self):
        """Test address serialization to dict."""
        address = Address(
            address_line1="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        data = address.model_dump()
        assert data["address_line1"] == "123 Main St"
        assert data["city"] == "New York"
        assert data["country"] == "US"

    def test_address_from_dict(self):
        """Test address creation from dict."""
        data = {
            "address_line1": "123 Main St",
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "US",
        }
        address = Address.model_validate(data)
        assert address.address_line1 == "123 Main St"
        assert address.city == "New York"
