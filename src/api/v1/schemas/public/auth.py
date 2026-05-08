"""Authentication Pydantic schemas."""

import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr
from src.config import settings as _app_settings

# Special-use TLDs (RFC 2606 / RFC 6761) that pydantic's EmailStr rejects by default.
# In non-production environments we accept them so QA/test fixtures work.
_RESERVED_TLDS = (".test", ".example", ".invalid", ".localhost")


def _email_with_dev_tld_passthrough(v: str) -> str:
    """If running in non-production, swap a reserved TLD for `.com` before
    EmailStr validates, so test fixtures like `qa+1@numu.test` are accepted.
    The actual stored email keeps its original form via a parallel field, but
    here we simply allow the validator to pass."""
    if not isinstance(v, str):
        return v
    if _app_settings.environment == "production":
        return v
    lower = v.lower()
    for tld in _RESERVED_TLDS:
        if lower.endswith(tld):
            return re.sub(re.escape(tld) + r"$", ".com", v, flags=re.IGNORECASE)
    return v


DevEmailStr = Annotated[EmailStr, BeforeValidator(_email_with_dev_tld_passthrough)]


class RegisterRequest(BaseModel):
    """Registration request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "yousef@numu.com",
                "password": "SecureP@ss123",
                "first_name": "Yousef",
                "last_name": "Yahia",
                "phone": "+201001234567",
            }
        }
    )

    email: DevEmailStr = Field(description="User email address")
    password: str = Field(
        ..., min_length=8, max_length=128, description="Password (8-128 characters)"
    )
    first_name: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="First name"
    )
    last_name: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="Last name"
    )
    phone: str | None = Field(None, max_length=20, description="Phone number")


class LoginRequest(BaseModel):
    """Login request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "yousef@numu.com",
                "password": "SecureP@ss123",
            }
        }
    )

    email: DevEmailStr = Field(description="User email address")
    password: str = Field(description="User password")


class UserResponse(BaseModel):
    """User response schema."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "yousef@numu.com",
                "first_name": "Yousef",
                "last_name": "Yahia",
                "full_name": "Yousef Yahia",
                "role": "merchant",
                "status": "active",
                "phone": "+201001234567",
                "avatar_url": None,
                "is_verified": True,
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z",
            }
        },
    )

    id: str = Field(description="User UUID")
    email: str = Field(description="User email")
    first_name: str = Field(description="First name")
    last_name: str = Field(description="Last name")
    full_name: str = Field(description="Concatenated full name")
    role: str = Field(description="User role: merchant, admin")
    status: str = Field(description="Account status: active, suspended, pending")
    phone: str | None = Field(description="Phone number")
    avatar_url: str | None = Field(description="Profile avatar URL")
    is_verified: bool = Field(description="Whether email is verified")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
    trial_ends_at: str | None = Field(None, description="Trial period end date")

    # Tenant lifecycle info (populated by GET /auth/me)
    tenant: "TenantInfoResponse | None" = Field(
        None,
        description="Tenant lifecycle state. Present when user owns a tenant.",
    )


class TenantInfoResponse(BaseModel):
    """Lightweight tenant info embedded in the /auth/me response.

    Gives the merchant hub enough info to show demo banners,
    trial countdowns, and read-only warnings without a separate API call.
    """

    id: str
    name: str
    subdomain: str
    plan: str
    lifecycle_state: str
    is_demo: bool
    is_on_trial: bool
    is_read_only: bool
    is_writable: bool
    expires_at: str | None = None
    days_remaining: int | None = None
    # Captured email from the Try-a-Demo form, so the merchant hub can
    # prefill the demo\u2192trial upgrade form. Only populated for demo tenants.
    demo_email: str | None = None
    # Per-tenant feature flags. Read by the merchant hub to gate the
    # `/marketing/promotions` nav and offers-v2 surfaces during phased
    # rollout. Empty `{}` means no flags enabled (legacy default).
    feature_flags: dict[str, bool] = {}


# Keep TokenResponse for internal use / backward compat with use-case DTOs
class TokenResponse(BaseModel):
    """Token response schema (internal — not exposed to clients)."""

    access_token: str = Field(description="JWT access token")
    refresh_token: str = Field(description="JWT refresh token")
    token_type: str = Field("bearer", description="Token type (always 'bearer')")


class TokenHandoffRequest(BaseModel):
    """Token hand-off from landing page → dashboard via URL params."""

    access_token: str = Field(description="JWT access token from redirect URL")
    refresh_token: str = Field(description="JWT refresh token from redirect URL")


class AuthResponse(BaseModel):
    """Auth response — tokens are in httpOnly cookies AND in the body for SPA redirects.

    When 2FA is required, `requires_2fa` is True and `challenge_token` is set.
    In that case `user` and `tokens` will be None.
    """

    user: UserResponse | None = Field(None, description="User profile")
    tokens: TokenResponse | None = Field(
        None,
        description="JWT tokens (also set as httpOnly cookies)",
    )
    requires_2fa: bool = Field(
        default=False,
        description="True when login requires a 2FA code to continue",
    )
    challenge_token: str | None = Field(
        None,
        description="Short-lived token to exchange at /auth/2fa/complete-login",
    )


class CsrfTokenResponse(BaseModel):
    """CSRF token response."""

    csrf_token: str = Field(description="CSRF token (also set as cookie)")


class PasswordResetRequest(BaseModel):
    """Password reset request schema."""

    email: EmailStr = Field(description="Email address to send reset link")


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation schema."""

    token: str = Field(description="Password reset token from email")
    new_password: str = Field(
        ..., min_length=8, max_length=128, description="New password"
    )


class ChangePasswordRequest(BaseModel):
    """Change password request schema."""

    current_password: str = Field(description="Current password")
    new_password: str = Field(
        ..., min_length=8, max_length=128, description="New password"
    )


class UpdateProfileRequest(BaseModel):
    """Update profile request schema."""

    first_name: SanitizedStr | None = Field(
        None, min_length=1, max_length=100, description="First name"
    )
    last_name: SanitizedStr | None = Field(
        None, min_length=1, max_length=100, description="Last name"
    )
    phone: str | None = Field(None, max_length=20, description="Phone number")
    avatar_url: str | None = Field(
        None, max_length=500, description="Profile avatar URL"
    )


class VerifyEmailRequest(BaseModel):
    """Email verification request schema (link-based)."""

    token: str = Field(
        description="Email verification token from the verification link"
    )


class VerifyEmailCodeRequest(BaseModel):
    """Email verification request schema (code-based)."""

    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit verification code sent to the user's email",
    )


class ResendVerificationRequest(BaseModel):
    """Request to resend the verification email."""

    pass  # Uses the authenticated user's session — no body needed
