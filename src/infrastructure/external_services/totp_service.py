"""TOTP (Time-based One-Time Password) service implementation.

This module implements TOTP functionality for Two-Factor Authentication
using the pyotp library. It provides:
- TOTP secret generation
- Provisioning URI generation for QR codes
- TOTP code verification
- Backup code generation and verification
"""

import secrets
import string

import pyotp
from passlib.context import CryptContext

from src.core.interfaces.services.totp_service import ITOTPService


class TOTPService(ITOTPService):
    """TOTP service implementation using pyotp.

    This service handles all TOTP-related operations for 2FA,
    including secret management, code verification, and backup codes.
    """

    # Backup code configuration
    BACKUP_CODE_LENGTH = 8  # Length of each backup code
    BACKUP_CODE_CHARSET = string.ascii_uppercase + string.digits  # A-Z, 0-9

    def __init__(self) -> None:
        """Initialize the TOTP service."""
        # Use bcrypt for backup code hashing (same as passwords)
        self._backup_code_context = CryptContext(
            schemes=["bcrypt"],
            deprecated="auto",
        )

    def generate_secret(self) -> str:
        """Generate a new TOTP secret.

        Uses pyotp's random_base32() which generates a secure
        32-character base32 secret suitable for TOTP.

        Returns:
            A base32-encoded secret string
        """
        return pyotp.random_base32()

    def generate_provisioning_uri(
        self,
        secret: str,
        email: str,
        issuer: str = "NUMU",
    ) -> str:
        """Generate a provisioning URI for QR code generation.

        The URI follows the otpauth:// format that is recognized
        by authenticator apps like Google Authenticator, Authy, etc.

        Format: otpauth://totp/{issuer}:{email}?secret={secret}&issuer={issuer}

        Args:
            secret: The base32-encoded TOTP secret
            email: User's email address (used as account name)
            issuer: The service name shown in authenticator app

        Returns:
            An otpauth:// URI string for QR code generation
        """
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=email, issuer_name=issuer)

    def verify_code(self, secret: str, code: str) -> bool:
        """Verify a TOTP code against the secret.

        This method validates the provided 6-digit code against
        the expected code generated from the secret. It allows
        for a small time window (1 step = 30 seconds before/after)
        to account for clock drift.

        Args:
            secret: The base32-encoded TOTP secret
            code: The 6-digit TOTP code to verify

        Returns:
            True if the code is valid, False otherwise
        """
        if not code or not secret:
            return False

        # Normalize code - remove spaces and ensure string
        normalized_code = str(code).replace(" ", "").strip()

        # Validate code format (should be 6 digits)
        if not normalized_code.isdigit() or len(normalized_code) != 6:
            return False

        try:
            totp = pyotp.TOTP(secret)
            # valid_window=1 allows codes from 30 seconds before/after
            return totp.verify(normalized_code, valid_window=1)
        except Exception:
            # Invalid secret or other error
            return False

    def generate_backup_codes(self, count: int = 10) -> list[str]:
        """Generate a list of backup codes for recovery.

        Backup codes are cryptographically random strings that
        can be used once each when the user doesn't have access
        to their authenticator app.

        Format: XXXX-XXXX (8 characters with dash in middle)

        Args:
            count: Number of backup codes to generate (default: 10)

        Returns:
            List of plaintext backup codes
        """
        codes = []
        for _ in range(count):
            # Generate random code using secure random
            code = "".join(
                secrets.choice(self.BACKUP_CODE_CHARSET)
                for _ in range(self.BACKUP_CODE_LENGTH)
            )
            # Format as XXXX-XXXX for readability
            formatted_code = f"{code[:4]}-{code[4:]}"
            codes.append(formatted_code)
        return codes

    def hash_backup_code(self, code: str) -> str:
        """Hash a backup code for secure storage.

        Uses bcrypt hashing (same as passwords) for security.
        The code is normalized before hashing.

        Args:
            code: The plaintext backup code

        Returns:
            The bcrypt-hashed backup code
        """
        # Normalize: remove dashes and convert to uppercase
        normalized = code.replace("-", "").upper().strip()
        return self._backup_code_context.hash(normalized)

    def verify_backup_code(self, code: str, hashed_code: str) -> bool:
        """Verify a backup code against its hash.

        Args:
            code: The plaintext backup code to verify
            hashed_code: The stored bcrypt hash to verify against

        Returns:
            True if the code matches the hash, False otherwise
        """
        if not code or not hashed_code:
            return False

        # Normalize: remove dashes and convert to uppercase
        normalized = code.replace("-", "").upper().strip()

        try:
            return self._backup_code_context.verify(normalized, hashed_code)
        except Exception:
            return False

    def get_current_code(self, secret: str) -> str:
        """Get the current TOTP code for a secret.

        This is primarily useful for testing purposes.

        Args:
            secret: The base32-encoded TOTP secret

        Returns:
            The current 6-digit TOTP code
        """
        totp = pyotp.TOTP(secret)
        return totp.now()


# Singleton instance for dependency injection
totp_service = TOTPService()
