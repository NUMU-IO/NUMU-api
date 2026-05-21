"""Two-Factor Authentication (2FA) entity.

This module defines the TwoFactorAuth entity that stores 2FA configuration
for users, including TOTP secrets and backup codes.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class TwoFactorMethod(StrEnum):
    """Supported 2FA methods."""

    TOTP = "totp"  # Time-based One-Time Password (Google Authenticator, etc.)
    # Future methods can be added here:
    # SMS = "sms"
    # EMAIL = "email"
    # WEBAUTHN = "webauthn"


class TwoFactorStatus(StrEnum):
    """Status of 2FA setup."""

    DISABLED = "disabled"  # 2FA not enabled
    PENDING = "pending"  # Secret generated, awaiting verification
    ENABLED = "enabled"  # 2FA active and verified


class TwoFactorAuth(BaseEntity):
    """Two-Factor Authentication configuration for a user.

    This entity stores the TOTP secret and backup codes for a user's 2FA.
    The secret is used to generate and verify TOTP codes, while backup
    codes provide a recovery mechanism if the user loses their authenticator.

    Attributes:
        user_id: The UUID of the user this 2FA belongs to
        method: The 2FA method (currently only TOTP)
        status: Current status of 2FA setup
        secret: The TOTP secret key (base32 encoded)
        backup_codes: List of hashed backup codes for recovery
        backup_codes_remaining: Count of unused backup codes
        verified_at: When 2FA was successfully verified/enabled
        last_used_at: When 2FA was last used for authentication
    """

    user_id: UUID
    method: TwoFactorMethod = TwoFactorMethod.TOTP
    status: TwoFactorStatus = TwoFactorStatus.DISABLED
    secret: str | None = None  # Base32-encoded TOTP secret
    backup_codes: list[str] = Field(default_factory=list)  # Hashed backup codes
    backup_codes_remaining: int = 0
    verified_at: datetime | None = None
    last_used_at: datetime | None = None

    @property
    def is_enabled(self) -> bool:
        """Check if 2FA is fully enabled and verified."""
        return self.status == TwoFactorStatus.ENABLED

    @property
    def is_pending(self) -> bool:
        """Check if 2FA setup is pending verification."""
        return self.status == TwoFactorStatus.PENDING

    @property
    def has_backup_codes(self) -> bool:
        """Check if user has remaining backup codes."""
        return self.backup_codes_remaining > 0

    def enable(self) -> None:
        """Mark 2FA as enabled after successful verification."""
        self.status = TwoFactorStatus.ENABLED
        self.verified_at = datetime.utcnow()
        self.touch()

    def disable(self) -> None:
        """Disable 2FA and clear all secrets."""
        self.status = TwoFactorStatus.DISABLED
        self.secret = None
        self.backup_codes = []
        self.backup_codes_remaining = 0
        self.verified_at = None
        self.touch()

    def set_pending(self, secret: str, hashed_backup_codes: list[str]) -> None:
        """Set 2FA to pending status with new secret and backup codes.

        Args:
            secret: The base32-encoded TOTP secret
            hashed_backup_codes: List of hashed backup codes
        """
        self.status = TwoFactorStatus.PENDING
        self.secret = secret
        self.backup_codes = hashed_backup_codes
        self.backup_codes_remaining = len(hashed_backup_codes)
        self.touch()

    def use_backup_code(self, used_code_hash: str) -> bool:
        """Mark a backup code as used.

        Args:
            used_code_hash: The hash of the backup code that was used

        Returns:
            True if code was found and removed, False otherwise
        """
        if used_code_hash in self.backup_codes:
            self.backup_codes.remove(used_code_hash)
            self.backup_codes_remaining = len(self.backup_codes)
            self.touch()
            return True
        return False

    def record_use(self) -> None:
        """Record that 2FA was used for authentication."""
        self.last_used_at = datetime.utcnow()
        self.touch()

    def regenerate_backup_codes(self, hashed_backup_codes: list[str]) -> None:
        """Replace backup codes with new ones.

        Args:
            hashed_backup_codes: List of newly hashed backup codes
        """
        self.backup_codes = hashed_backup_codes
        self.backup_codes_remaining = len(hashed_backup_codes)
        self.touch()
