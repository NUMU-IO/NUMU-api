"""Tests for TOTP service."""

import pytest

from src.infrastructure.external_services.totp_service import TOTPService


class TestTOTPService:
    """Tests for the TOTP service implementation."""

    @pytest.fixture
    def service(self) -> TOTPService:
        """Create a TOTP service instance."""
        return TOTPService()

    def test_generate_secret_returns_valid_base32(self, service: TOTPService):
        """Test that generate_secret returns a valid base32 string."""
        secret = service.generate_secret()

        assert secret is not None
        assert len(secret) == 32  # pyotp generates 32-char base32 secrets
        assert secret.isalnum()  # base32 is alphanumeric

    def test_generate_secret_returns_unique_values(self, service: TOTPService):
        """Test that each call to generate_secret returns a unique value."""
        secrets = [service.generate_secret() for _ in range(10)]

        # All secrets should be unique
        assert len(set(secrets)) == 10

    def test_generate_provisioning_uri_format(self, service: TOTPService):
        """Test that provisioning URI has correct otpauth format."""
        secret = service.generate_secret()
        email = "test@example.com"
        issuer = "TestApp"

        uri = service.generate_provisioning_uri(secret, email, issuer)

        assert uri.startswith("otpauth://totp/")
        assert issuer in uri
        assert "secret=" in uri
        assert secret in uri

    def test_generate_provisioning_uri_default_issuer(self, service: TOTPService):
        """Test that default issuer is NUMU."""
        secret = service.generate_secret()
        email = "test@example.com"

        uri = service.generate_provisioning_uri(secret, email)

        assert "NUMU" in uri

    def test_verify_code_with_valid_code(self, service: TOTPService):
        """Test that a valid TOTP code is verified correctly."""
        secret = service.generate_secret()

        # Get the current valid code
        current_code = service.get_current_code(secret)

        # Verify it
        assert service.verify_code(secret, current_code) is True

    def test_verify_code_with_invalid_code(self, service: TOTPService):
        """Test that an invalid TOTP code is rejected."""
        secret = service.generate_secret()

        # Try various invalid codes
        assert service.verify_code(secret, "000000") is False
        assert service.verify_code(secret, "123456") is False
        assert service.verify_code(secret, "abcdef") is False

    def test_verify_code_with_empty_inputs(self, service: TOTPService):
        """Test that empty inputs return False."""
        secret = service.generate_secret()

        assert service.verify_code("", "123456") is False
        assert service.verify_code(secret, "") is False
        assert service.verify_code("", "") is False

    def test_verify_code_with_spaces(self, service: TOTPService):
        """Test that codes with spaces are handled correctly."""
        secret = service.generate_secret()
        current_code = service.get_current_code(secret)

        # Add spaces to the code
        spaced_code = f"{current_code[:3]} {current_code[3:]}"

        assert service.verify_code(secret, spaced_code) is True

    def test_verify_code_rejects_wrong_length(self, service: TOTPService):
        """Test that codes with wrong length are rejected."""
        secret = service.generate_secret()

        assert service.verify_code(secret, "12345") is False  # Too short
        assert service.verify_code(secret, "1234567") is False  # Too long

    def test_generate_backup_codes_count(self, service: TOTPService):
        """Test that correct number of backup codes are generated."""
        codes = service.generate_backup_codes(count=10)

        assert len(codes) == 10

    def test_generate_backup_codes_custom_count(self, service: TOTPService):
        """Test generating custom number of backup codes."""
        codes = service.generate_backup_codes(count=5)
        assert len(codes) == 5

        codes = service.generate_backup_codes(count=15)
        assert len(codes) == 15

    def test_generate_backup_codes_format(self, service: TOTPService):
        """Test that backup codes have correct format (XXXX-XXXX)."""
        codes = service.generate_backup_codes(count=5)

        for code in codes:
            assert len(code) == 9  # XXXX-XXXX = 9 chars
            assert code[4] == "-"
            assert code[:4].isalnum()
            assert code[5:].isalnum()

    def test_generate_backup_codes_uniqueness(self, service: TOTPService):
        """Test that backup codes are unique."""
        codes = service.generate_backup_codes(count=100)

        assert len(set(codes)) == 100

    def test_hash_backup_code(self, service: TOTPService):
        """Test that backup codes are hashed correctly."""
        code = "ABCD-EFGH"
        hashed = service.hash_backup_code(code)

        assert hashed is not None
        assert hashed != code
        assert hashed.startswith("$2b$")  # bcrypt prefix

    def test_verify_backup_code_valid(self, service: TOTPService):
        """Test that valid backup code is verified correctly."""
        code = "ABCD-EFGH"
        hashed = service.hash_backup_code(code)

        assert service.verify_backup_code(code, hashed) is True

    def test_verify_backup_code_invalid(self, service: TOTPService):
        """Test that invalid backup code is rejected."""
        code = "ABCD-EFGH"
        hashed = service.hash_backup_code(code)

        assert service.verify_backup_code("WRONG-CODE", hashed) is False

    def test_verify_backup_code_case_insensitive(self, service: TOTPService):
        """Test that backup code verification is case insensitive."""
        code = "ABCD-EFGH"
        hashed = service.hash_backup_code(code)

        # Should work with lowercase
        assert service.verify_backup_code("abcd-efgh", hashed) is True

    def test_verify_backup_code_without_dash(self, service: TOTPService):
        """Test that backup codes work without dashes."""
        code = "ABCD-EFGH"
        hashed = service.hash_backup_code(code)

        # Should work without dash
        assert service.verify_backup_code("ABCDEFGH", hashed) is True

    def test_verify_backup_code_with_spaces(self, service: TOTPService):
        """Test that backup codes work with extra spaces."""
        code = "ABCD-EFGH"
        hashed = service.hash_backup_code(code)

        assert service.verify_backup_code("  ABCD-EFGH  ", hashed) is True

    def test_verify_backup_code_empty_inputs(self, service: TOTPService):
        """Test that empty inputs return False."""
        code = "ABCD-EFGH"
        hashed = service.hash_backup_code(code)

        assert service.verify_backup_code("", hashed) is False
        assert service.verify_backup_code(code, "") is False
        assert service.verify_backup_code("", "") is False

    def test_get_current_code_format(self, service: TOTPService):
        """Test that current code is 6 digits."""
        secret = service.generate_secret()
        code = service.get_current_code(secret)

        assert len(code) == 6
        assert code.isdigit()
