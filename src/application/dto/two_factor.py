"""Two-Factor Authentication DTOs."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.two_factor import TwoFactorAuth, TwoFactorStatus


@dataclass
class Enable2FADTO(BaseDTO):
    """Response when enabling 2FA.

    Contains the provisioning URI for QR code and backup codes.
    """

    secret: str
    provisioning_uri: str
    qr_code_uri: str  # Same as provisioning_uri, for clarity
    backup_codes: list[str]  # Plaintext codes (only shown once!)
    method: str = "totp"


@dataclass
class TwoFactorStatusDTO(BaseDTO):
    """Current 2FA status for a user."""

    is_enabled: bool
    method: str | None
    backup_codes_remaining: int
    enabled_at: datetime | None
    last_used_at: datetime | None

    @classmethod
    def from_entity(cls, entity: TwoFactorAuth | None) -> "TwoFactorStatusDTO":
        """Create DTO from TwoFactorAuth entity."""
        if entity is None or entity.status == TwoFactorStatus.DISABLED:
            return cls(
                is_enabled=False,
                method=None,
                backup_codes_remaining=0,
                enabled_at=None,
                last_used_at=None,
            )

        return cls(
            is_enabled=entity.is_enabled,
            method=entity.method.value if entity.method else None,
            backup_codes_remaining=entity.backup_codes_remaining,
            enabled_at=entity.verified_at,
            last_used_at=entity.last_used_at,
        )


@dataclass
class Verify2FADTO(BaseDTO):
    """Request to verify a 2FA code."""

    code: str  # 6-digit TOTP code or backup code


@dataclass
class Verify2FAResponseDTO(BaseDTO):
    """Response after verifying 2FA code."""

    verified: bool
    method_used: str  # "totp" or "backup_code"
    backup_codes_remaining: int | None = None  # Only set if backup code was used


@dataclass
class Disable2FADTO(BaseDTO):
    """Request to disable 2FA."""

    password: str  # User's password for confirmation
    code: str | None = None  # Optional TOTP code for additional security


@dataclass
class RegenerateBackupCodesDTO(BaseDTO):
    """Response when regenerating backup codes."""

    backup_codes: list[str]  # New plaintext backup codes
    previous_count: int
    new_count: int


@dataclass
class TwoFactorChallengeDTO(BaseDTO):
    """Challenge presented when 2FA is required during login."""

    challenge_token: str  # Temporary token for completing 2FA
    methods_available: list[str]  # ["totp", "backup_code"]
    user_id: UUID
