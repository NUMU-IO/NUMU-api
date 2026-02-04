"""Secrets Manager for secure credential encryption and decryption.

This service handles all encryption/decryption operations for sensitive credentials
such as API keys, secrets, and tokens for payment gateways, shipping carriers, etc.

SECURITY NOTES:
1. Master key MUST be stored securely (environment variable in dev, secrets manager in prod)
2. Each credential set uses a derived key based on key_id for key rotation support
3. Credentials are encrypted using Fernet (AES-128-CBC with HMAC-SHA256)
4. In production, consider integrating with:
   - AWS Secrets Manager
   - HashiCorp Vault
   - Google Cloud Secret Manager
   - Azure Key Vault
"""

import base64
import json
import os
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SecretsManagerError(Exception):
    """Base exception for secrets manager errors."""
    pass


class EncryptionError(SecretsManagerError):
    """Raised when encryption fails."""
    pass


class DecryptionError(SecretsManagerError):
    """Raised when decryption fails."""
    pass


class KeyNotFoundError(SecretsManagerError):
    """Raised when encryption key is not configured."""
    pass


class SecretsManager:
    """Manages encryption and decryption of sensitive credentials.

    This class provides a secure way to encrypt and decrypt credentials
    using AES-256 encryption with key derivation.

    Usage:
        secrets_manager = SecretsManager()

        # Encrypt credentials
        key_id = await secrets_manager.get_current_key_id()
        encrypted = await secrets_manager.encrypt(
            data={"api_key": "secret123", "merchant_id": "M001"},
            key_id=key_id
        )

        # Decrypt credentials
        decrypted = await secrets_manager.decrypt(encrypted, key_id)
    """

    # Key derivation parameters
    _KDF_ITERATIONS = 100_000
    _KDF_LENGTH = 32

    def __init__(self, master_key: str | None = None):
        """Initialize the secrets manager.

        Args:
            master_key: Optional master key override. If not provided,
                       reads from CREDENTIAL_ENCRYPTION_KEY environment variable.

        Raises:
            KeyNotFoundError: If no master key is configured.
        """
        self._master_key = master_key or os.environ.get("CREDENTIAL_ENCRYPTION_KEY")

        if not self._master_key:
            raise KeyNotFoundError(
                "CREDENTIAL_ENCRYPTION_KEY environment variable is not set. "
                "This is required for secure credential storage."
            )

        # Validate master key length (should be at least 32 characters)
        if len(self._master_key) < 32:
            raise KeyNotFoundError(
                "CREDENTIAL_ENCRYPTION_KEY must be at least 32 characters long."
            )

    def _derive_key(self, key_id: str) -> bytes:
        """Derive an encryption key from the master key and key_id.

        Uses PBKDF2 with SHA-256 for key derivation. The key_id acts as
        a salt, allowing different keys for different versions/purposes.

        Args:
            key_id: Unique identifier for this key version.

        Returns:
            32-byte derived key suitable for Fernet encryption.
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self._KDF_LENGTH,
            salt=key_id.encode("utf-8"),
            iterations=self._KDF_ITERATIONS,
        )
        derived_key = kdf.derive(self._master_key.encode("utf-8"))
        return base64.urlsafe_b64encode(derived_key)

    def _get_fernet(self, key_id: str) -> Fernet:
        """Get a Fernet instance for the given key_id.

        Args:
            key_id: Unique identifier for this key version.

        Returns:
            Fernet instance configured with the derived key.
        """
        derived_key = self._derive_key(key_id)
        return Fernet(derived_key)

    async def get_current_key_id(self) -> str:
        """Get the current encryption key ID.

        The key ID includes a version and date component for rotation support.
        In production, this could be fetched from a key management service.

        Returns:
            Current key ID string (e.g., "numu-key-v1-202601")
        """
        # Key ID format: numu-key-v{version}-{YYYYMM}
        # Rotate monthly by default
        current_month = datetime.utcnow().strftime("%Y%m")
        return f"numu-key-v1-{current_month}"

    async def encrypt(self, data: dict[str, Any], key_id: str) -> bytes:
        """Encrypt a dictionary of credentials.

        Args:
            data: Dictionary containing credentials to encrypt.
            key_id: Key ID to use for encryption.

        Returns:
            Encrypted bytes that can be stored in the database.

        Raises:
            EncryptionError: If encryption fails.
        """
        try:
            fernet = self._get_fernet(key_id)
            json_data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            encrypted = fernet.encrypt(json_data)
            return encrypted
        except Exception as e:
            raise EncryptionError(f"Failed to encrypt credentials: {str(e)}") from e

    async def decrypt(self, encrypted_data: bytes, key_id: str) -> dict[str, Any]:
        """Decrypt encrypted credentials.

        Args:
            encrypted_data: Encrypted bytes from the database.
            key_id: Key ID that was used for encryption.

        Returns:
            Decrypted dictionary of credentials.

        Raises:
            DecryptionError: If decryption fails (invalid key, corrupted data, etc.)
        """
        try:
            fernet = self._get_fernet(key_id)
            decrypted = fernet.decrypt(encrypted_data)
            return json.loads(decrypted.decode("utf-8"))
        except InvalidToken:
            raise DecryptionError(
                "Failed to decrypt credentials: Invalid token. "
                "This may indicate wrong key_id or corrupted data."
            )
        except Exception as e:
            raise DecryptionError(f"Failed to decrypt credentials: {str(e)}") from e

    async def rotate_credentials(
        self,
        encrypted_data: bytes,
        old_key_id: str,
        new_key_id: str
    ) -> bytes:
        """Re-encrypt credentials with a new key.

        Used for key rotation. Decrypts with old key and re-encrypts with new key.

        Args:
            encrypted_data: Currently encrypted credentials.
            old_key_id: Key ID used for current encryption.
            new_key_id: New key ID to use for re-encryption.

        Returns:
            Newly encrypted bytes.

        Raises:
            DecryptionError: If decryption with old key fails.
            EncryptionError: If re-encryption with new key fails.
        """
        # Decrypt with old key
        decrypted = await self.decrypt(encrypted_data, old_key_id)

        # Re-encrypt with new key
        return await self.encrypt(decrypted, new_key_id)

    def mask_credential(self, value: str, visible_chars: int = 4) -> str:
        """Mask a credential value for display purposes.

        Args:
            value: The credential value to mask.
            visible_chars: Number of characters to show at the end.

        Returns:
            Masked string (e.g., "***4567")
        """
        if len(value) <= visible_chars:
            return "*" * len(value)
        return "*" * (len(value) - visible_chars) + value[-visible_chars:]

    async def validate_key_id(self, key_id: str) -> bool:
        """Validate that a key_id is in the correct format.

        Args:
            key_id: Key ID to validate.

        Returns:
            True if valid, False otherwise.
        """
        # Expected format: numu-key-v{version}-{YYYYMM}
        if not key_id.startswith("numu-key-v"):
            return False

        parts = key_id.split("-")
        if len(parts) != 4:
            return False

        # Check version is numeric
        version = parts[2].replace("v", "")
        if not version.isdigit():
            return False

        # Check date format
        date_part = parts[3]
        if len(date_part) != 6 or not date_part.isdigit():
            return False

        return True


# Singleton instance for convenience
_secrets_manager: SecretsManager | None = None


def get_secrets_manager() -> SecretsManager:
    """Get or create the singleton SecretsManager instance.

    Returns:
        SecretsManager instance.

    Raises:
        KeyNotFoundError: If encryption key is not configured.
    """
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretsManager()
    return _secrets_manager
