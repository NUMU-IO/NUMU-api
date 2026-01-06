"""Unit tests for value objects."""

import pytest

from src.core.value_objects import Address, Email, Money, PhoneNumber


class TestEmailValueObject:
    """Tests for the Email value object."""

    def test_valid_email(self):
        """Test creating email with valid value."""
        email = Email("test@example.com")
        assert email.value == "test@example.com"

    def test_email_normalization(self):
        """Test email is normalized to lowercase."""
        email = Email("TEST@EXAMPLE.COM")
        assert email.value == "test@example.com"

    def test_invalid_email_raises_error(self):
        """Test invalid email raises ValueError."""
        with pytest.raises(ValueError):
            Email("invalid-email")

    def test_empty_email_raises_error(self):
        """Test empty email raises ValueError."""
        with pytest.raises(ValueError):
            Email("")

    def test_email_equality(self):
        """Test email equality."""
        email1 = Email("test@example.com")
        email2 = Email("TEST@EXAMPLE.COM")
        assert email1 == email2

    def test_email_hash(self):
        """Test email is hashable."""
        email = Email("test@example.com")
        assert hash(email) == hash("test@example.com")


class TestPhoneNumberValueObject:
    """Tests for the PhoneNumber value object."""

    def test_valid_phone(self):
        """Test creating phone with valid value."""
        phone = PhoneNumber("+1234567890")
        assert phone.value == "+1234567890"

    def test_phone_normalization(self):
        """Test phone is normalized."""
        phone = PhoneNumber("+1 (234) 567-890")
        # Should strip spaces and formatting
        assert "234567890" in phone.value

    def test_invalid_phone_raises_error(self):
        """Test invalid phone raises ValueError."""
        with pytest.raises(ValueError):
            PhoneNumber("abc")

    def test_phone_equality(self):
        """Test phone equality."""
        phone1 = PhoneNumber("+1234567890")
        phone2 = PhoneNumber("+1234567890")
        assert phone1 == phone2


class TestMoneyValueObject:
    """Tests for the Money value object."""

    def test_money_creation(self):
        """Test creating money with valid values."""
        money = Money(amount=1000, currency="USD")
        assert money.amount == 1000
        assert money.currency == "USD"

    def test_money_formatted_amount(self):
        """Test formatted amount property."""
        money = Money(amount=1999, currency="USD")
        assert money.formatted_amount == 19.99

    def test_money_addition(self):
        """Test adding money values."""
        money1 = Money(amount=1000, currency="USD")
        money2 = Money(amount=500, currency="USD")
        result = money1.add(money2)
        assert result.amount == 1500
        assert result.currency == "USD"

    def test_money_addition_different_currency_raises_error(self):
        """Test adding money with different currencies raises error."""
        money1 = Money(amount=1000, currency="USD")
        money2 = Money(amount=500, currency="EUR")
        with pytest.raises(ValueError):
            money1.add(money2)

    def test_money_subtraction(self):
        """Test subtracting money values."""
        money1 = Money(amount=1000, currency="USD")
        money2 = Money(amount=500, currency="USD")
        result = money1.subtract(money2)
        assert result.amount == 500

    def test_negative_amount_raises_error(self):
        """Test negative amount raises ValueError."""
        with pytest.raises(ValueError):
            Money(amount=-100, currency="USD")


class TestAddressValueObject:
    """Tests for the Address value object."""

    def test_address_creation(self):
        """Test creating address with valid values."""
        address = Address(
            street="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        assert address.street == "123 Main St"
        assert address.city == "New York"
        assert address.state == "NY"
        assert address.postal_code == "10001"
        assert address.country == "US"

    def test_address_full_address(self):
        """Test full address string."""
        address = Address(
            street="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        full = address.full_address
        assert "123 Main St" in full
        assert "New York" in full
        assert "NY" in full

    def test_address_equality(self):
        """Test address equality."""
        addr1 = Address(
            street="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        addr2 = Address(
            street="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="US",
        )
        assert addr1 == addr2
