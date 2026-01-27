"""Unit tests for Email value object."""

import pytest
from pydantic import ValidationError

from src.core.value_objects.email import Email


class TestEmail:
    """Tests for Email value object."""

    def test_valid_email(self):
        """Test valid email creation."""
        email = Email(value="user@example.com")

        assert email.value == "user@example.com"
        assert str(email) == "user@example.com"

    def test_email_normalization_lowercase(self):
        """Test email is normalized to lowercase."""
        email = Email(value="USER@EXAMPLE.COM")

        assert email.value == "user@example.com"

    def test_email_normalization_strips_whitespace(self):
        """Test email strips whitespace."""
        email = Email(value="  user@example.com  ")

        assert email.value == "user@example.com"

    def test_invalid_email_no_at_sign(self):
        """Test validation fails for email without @ sign."""
        with pytest.raises(ValidationError):
            Email(value="userexample.com")

    def test_invalid_email_no_domain(self):
        """Test validation fails for email without domain."""
        with pytest.raises(ValidationError):
            Email(value="user@")

    def test_invalid_email_no_tld(self):
        """Test validation fails for email without TLD."""
        with pytest.raises(ValidationError):
            Email(value="user@example")

    def test_invalid_email_short_tld(self):
        """Test validation fails for email with single-char TLD."""
        with pytest.raises(ValidationError):
            Email(value="user@example.c")

    def test_invalid_email_empty(self):
        """Test validation fails for empty email."""
        with pytest.raises(ValidationError):
            Email(value="")

    def test_email_equality_same_email(self):
        """Test equality with same email."""
        email1 = Email(value="user@example.com")
        email2 = Email(value="user@example.com")

        assert email1 == email2

    def test_email_equality_different_case(self):
        """Test equality with different case (normalized)."""
        email1 = Email(value="user@example.com")
        email2 = Email(value="USER@EXAMPLE.COM")

        assert email1 == email2

    def test_email_equality_with_string(self):
        """Test equality comparison with string."""
        email = Email(value="user@example.com")

        assert email == "user@example.com"
        assert email == "USER@EXAMPLE.COM"  # Case insensitive

    def test_email_inequality(self):
        """Test inequality with different email."""
        email1 = Email(value="user1@example.com")
        email2 = Email(value="user2@example.com")

        assert email1 != email2

    def test_email_hash(self):
        """Test email can be used in sets and dicts."""
        email1 = Email(value="user@example.com")
        email2 = Email(value="USER@EXAMPLE.COM")

        # Same email should have same hash
        assert hash(email1) == hash(email2)

        # Can be used in set
        email_set = {email1, email2}
        assert len(email_set) == 1

    def test_email_immutability(self):
        """Test email is immutable (frozen)."""
        email = Email(value="user@example.com")

        with pytest.raises(Exception):  # ValidationError for frozen model
            email.value = "other@example.com"

    def test_valid_email_with_plus_sign(self):
        """Test valid email with plus sign."""
        email = Email(value="user+tag@example.com")

        assert email.value == "user+tag@example.com"

    def test_valid_email_with_dots(self):
        """Test valid email with dots in local part."""
        email = Email(value="user.name.test@example.com")

        assert email.value == "user.name.test@example.com"

    def test_valid_email_with_subdomain(self):
        """Test valid email with subdomain."""
        email = Email(value="user@mail.example.com")

        assert email.value == "user@mail.example.com"
