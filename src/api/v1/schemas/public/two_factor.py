"""Two-Factor Authentication Pydantic schemas."""

from pydantic import BaseModel, Field


class Enable2FAResponse(BaseModel):
    """Response when 2FA is enabled.

    Contains the provisioning URI for QR code generation and backup codes.
    The backup codes should be saved by the user as they are only shown once.
    """

    secret: str = Field(
        ...,
        description="Base32-encoded TOTP secret (for manual entry)",
    )
    provisioning_uri: str = Field(
        ...,
        description="otpauth:// URI for QR code generation",
    )
    qr_code_uri: str = Field(
        ...,
        description="Same as provisioning_uri, for clarity",
    )
    backup_codes: list[str] = Field(
        ...,
        description="10 backup codes for recovery (shown only once!)",
    )
    method: str = Field(
        default="totp",
        description="2FA method used",
    )


class Verify2FARequest(BaseModel):
    """Request to verify a 2FA code.

    Can be either a 6-digit TOTP code or a backup code (XXXX-XXXX format).
    """

    code: str = Field(
        ...,
        min_length=6,
        max_length=10,
        description="6-digit TOTP code or backup code (XXXX-XXXX)",
    )


class Verify2FAResponse(BaseModel):
    """Response after verifying a 2FA code."""

    verified: bool = Field(
        ...,
        description="Whether the code was valid",
    )
    method_used: str = Field(
        ...,
        description="Method used: 'totp' or 'backup_code'",
    )
    backup_codes_remaining: int | None = Field(
        None,
        description="Remaining backup codes (only if backup code was used)",
    )


class Disable2FARequest(BaseModel):
    """Request to disable 2FA.

    Requires password confirmation for security.
    """

    password: str = Field(
        ...,
        description="User's password for confirmation",
    )
    code: str | None = Field(
        None,
        description="Optional TOTP code for additional security",
    )


class TwoFactorStatusResponse(BaseModel):
    """Current 2FA status for a user."""

    is_enabled: bool = Field(
        ...,
        description="Whether 2FA is currently enabled",
    )
    method: str | None = Field(
        None,
        description="2FA method if enabled",
    )
    backup_codes_remaining: int = Field(
        default=0,
        description="Number of unused backup codes",
    )
    enabled_at: str | None = Field(
        None,
        description="When 2FA was enabled (ISO timestamp)",
    )
    last_used_at: str | None = Field(
        None,
        description="When 2FA was last used (ISO timestamp)",
    )


class RegenerateBackupCodesRequest(BaseModel):
    """Request to regenerate backup codes."""

    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        description="Current 6-digit TOTP code for verification",
    )


class RegenerateBackupCodesResponse(BaseModel):
    """Response with new backup codes."""

    backup_codes: list[str] = Field(
        ...,
        description="New backup codes (shown only once!)",
    )
    previous_count: int = Field(
        ...,
        description="How many backup codes were remaining before regeneration",
    )
    new_count: int = Field(
        ...,
        description="Number of new backup codes generated",
    )


class TwoFactorChallengeResponse(BaseModel):
    """Response when login requires 2FA verification.

    Returned instead of tokens when user has 2FA enabled.
    """

    requires_2fa: bool = Field(
        default=True,
        description="Indicates 2FA verification is required",
    )
    challenge_token: str = Field(
        ...,
        description="Temporary token to complete 2FA (expires in 5 minutes)",
    )
    methods_available: list[str] = Field(
        default=["totp", "backup_code"],
        description="Available 2FA methods",
    )


class Complete2FALoginRequest(BaseModel):
    """Request to complete login with 2FA code."""

    challenge_token: str = Field(
        ...,
        description="Challenge token from initial login attempt",
    )
    code: str = Field(
        ...,
        min_length=6,
        max_length=10,
        description="6-digit TOTP code or backup code",
    )
