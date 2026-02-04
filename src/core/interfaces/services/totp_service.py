"""TOTP service interface for Two-Factor Authentication."""

from abc import ABC, abstractmethod


class ITOTPService(ABC):
    """Interface for Time-based One-Time Password (TOTP) service.

    This service handles TOTP secret generation, QR code URI creation,
    code verification, and backup code management for 2FA.
    """

    @abstractmethod
    def generate_secret(self) -> str:
        """Generate a new TOTP secret.

        Returns:
            A base32-encoded secret string suitable for TOTP generation.
        """
        ...

    @abstractmethod
    def generate_provisioning_uri(
        self,
        secret: str,
        email: str,
        issuer: str = "NUMU",
    ) -> str:
        """Generate a provisioning URI for QR code generation.

        This URI can be encoded into a QR code that authenticator apps
        can scan to set up TOTP.

        Args:
            secret: The base32-encoded TOTP secret
            email: User's email address (used as account name)
            issuer: The service name shown in authenticator app

        Returns:
            An otpauth:// URI string for QR code generation
        """
        ...

    @abstractmethod
    def verify_code(self, secret: str, code: str) -> bool:
        """Verify a TOTP code against the secret.

        Args:
            secret: The base32-encoded TOTP secret
            code: The 6-digit TOTP code to verify

        Returns:
            True if the code is valid, False otherwise
        """
        ...

    @abstractmethod
    def generate_backup_codes(self, count: int = 10) -> list[str]:
        """Generate a list of backup codes for recovery.

        Backup codes are single-use codes that can be used when
        the user doesn't have access to their authenticator app.

        Args:
            count: Number of backup codes to generate (default: 10)

        Returns:
            List of plaintext backup codes
        """
        ...

    @abstractmethod
    def hash_backup_code(self, code: str) -> str:
        """Hash a backup code for secure storage.

        Args:
            code: The plaintext backup code

        Returns:
            The hashed backup code
        """
        ...

    @abstractmethod
    def verify_backup_code(self, code: str, hashed_code: str) -> bool:
        """Verify a backup code against its hash.

        Args:
            code: The plaintext backup code to verify
            hashed_code: The stored hash to verify against

        Returns:
            True if the code matches the hash, False otherwise
        """
        ...
