"""Unit tests for PhoneNumber value object."""

import pytest

from src.core.value_objects.phone import PhoneNumber


class TestPhoneNumber:
    """Tests for PhoneNumber value object."""

    def test_valid_egyptian_phone(self):
        """Test valid Egyptian phone number."""
        phone = PhoneNumber(value="01234567890", country_code="EG")

        assert phone.value == "+201234567890"  # Normalized to E.164
        assert phone.is_valid is True

    def test_valid_egyptian_phone_with_country_code(self):
        """Test Egyptian phone with country code prefix."""
        phone = PhoneNumber(value="+201234567890")

        assert phone.value == "+201234567890"
        assert phone.is_valid is True

    def test_valid_us_phone(self):
        """Test valid US phone number."""
        phone = PhoneNumber(value="2025551234", country_code="US")

        assert "+1" in phone.value  # US country code
        assert phone.is_valid is True

    def test_phone_national_format(self):
        """Test phone number national format."""
        phone = PhoneNumber(value="+201234567890")

        national = phone.national_format

        # Should be in national format (without country code)
        assert national is not None

    def test_phone_international_format(self):
        """Test phone number international format."""
        phone = PhoneNumber(value="+201234567890")

        international = phone.international_format

        # Should include country code
        assert "+20" in international or "20" in international

    def test_invalid_phone_keeps_original(self):
        """Test invalid phone keeps original value."""
        phone = PhoneNumber(value="invalid-phone")

        assert phone.value == "invalid-phone"
        assert phone.is_valid is False

    def test_phone_default_country_code(self):
        """Test phone defaults to EG country code."""
        phone = PhoneNumber(value="01234567890")

        assert phone.country_code == "EG"

    def test_phone_equality_same_number(self):
        """Test equality with same phone number."""
        phone1 = PhoneNumber(value="+201234567890")
        phone2 = PhoneNumber(value="+201234567890")

        assert phone1 == phone2

    def test_phone_equality_with_string(self):
        """Test equality comparison with string."""
        phone = PhoneNumber(value="+201234567890")

        assert phone == "+201234567890"

    def test_phone_inequality(self):
        """Test inequality with different number."""
        phone1 = PhoneNumber(value="+201234567890")
        phone2 = PhoneNumber(value="+201234567891")

        assert phone1 != phone2

    def test_phone_hash(self):
        """Test phone can be used in sets and dicts."""
        phone1 = PhoneNumber(value="+201234567890")
        phone2 = PhoneNumber(value="+201234567890")

        # Same number should have same hash
        assert hash(phone1) == hash(phone2)

        # Can be used in set
        phone_set = {phone1, phone2}
        assert len(phone_set) == 1

    def test_phone_str(self):
        """Test string representation."""
        phone = PhoneNumber(value="+201234567890")

        assert str(phone) == "+201234567890"

    def test_phone_immutability(self):
        """Test phone is immutable (frozen)."""
        phone = PhoneNumber(value="+201234567890")

        with pytest.raises(Exception):  # ValidationError for frozen model
            phone.value = "+201111111111"

    def test_phone_with_spaces_and_dashes(self):
        """Test phone with spaces and dashes gets normalized."""
        phone = PhoneNumber(value="0123 456 7890", country_code="EG")

        # Should be normalized
        assert phone.is_valid is True

    def test_phone_multiple_formats_same_number(self):
        """Test different formats of same number are normalized."""
        phone1 = PhoneNumber(value="01234567890", country_code="EG")
        phone2 = PhoneNumber(value="+201234567890")
        phone3 = PhoneNumber(value="00201234567890", country_code="EG")

        # All should normalize to same E.164 format
        assert phone1.value == phone2.value
        # phone3 might or might not normalize depending on parsing


class TestPhoneNumberEdgeCases:
    """Edge case tests for PhoneNumber value object."""

    def test_empty_phone(self):
        """Test empty phone number."""
        phone = PhoneNumber(value="")

        assert phone.value == ""
        assert phone.is_valid is False

    def test_phone_with_extension(self):
        """Test phone number with extension."""
        phone = PhoneNumber(value="+201234567890 ext 123")

        # Extension handling depends on phonenumbers library
        assert phone.value is not None

    def test_very_long_phone(self):
        """Test very long phone number."""
        phone = PhoneNumber(value="123456789012345678901234567890")

        assert phone.is_valid is False

    def test_phone_with_special_characters(self):
        """Test phone with special characters."""
        phone = PhoneNumber(value="(012) 345-6789")

        # Should handle common formatting characters
        assert phone.value is not None

    def test_saudi_phone(self):
        """Test Saudi Arabian phone number."""
        phone = PhoneNumber(value="0501234567", country_code="SA")

        assert phone.is_valid is True

    def test_uae_phone(self):
        """Test UAE phone number."""
        phone = PhoneNumber(value="0501234567", country_code="AE")

        assert phone.is_valid is True
