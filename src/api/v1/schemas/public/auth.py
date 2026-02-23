"""Authentication Pydantic schemas."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr


class RegisterRequest(BaseModel):
    """Registration request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "yousef@numu.com",
                "password": "SecureP@ss123",
                "first_name": "Yousef",
                "last_name": "Khalil",
                "phone": "+201001234567",
            }
        }
    )

    email: EmailStr = Field(description="User email address")
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

    email: EmailStr = Field(description="User email address")
    password: str = Field(description="User password")


class TokenResponse(BaseModel):
    """Token response schema."""

    access_token: str = Field(description="JWT access token")
    refresh_token: str = Field(description="JWT refresh token")
    token_type: str = Field("bearer", description="Token type (always 'bearer')")


class UserResponse(BaseModel):
    """User response schema."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "yousef@numu.com",
                "first_name": "Yousef",
                "last_name": "Khalil",
                "full_name": "Yousef Khalil",
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


class AuthResponse(BaseModel):
    """Authentication response schema."""

    user: UserResponse = Field(description="User profile")
    tokens: TokenResponse = Field(description="Auth tokens")


class RefreshTokenRequest(BaseModel):
    """Refresh token request schema."""

    refresh_token: str = Field(description="JWT refresh token")


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
