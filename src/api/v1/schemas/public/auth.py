"""Authentication Pydantic schemas."""

from pydantic import BaseModel, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr


class RegisterRequest(BaseModel):
    """Registration request schema."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    first_name: SanitizedStr = Field(..., min_length=1, max_length=100)
    last_name: SanitizedStr = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=20)


class LoginRequest(BaseModel):
    """Login request schema."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Token response schema."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """User response schema."""

    id: str
    email: str
    first_name: str
    last_name: str
    full_name: str
    role: str
    status: str
    phone: str | None
    avatar_url: str | None
    is_verified: bool
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    """Authentication response schema."""

    user: UserResponse
    tokens: TokenResponse


class RefreshTokenRequest(BaseModel):
    """Refresh token request schema."""

    refresh_token: str


class PasswordResetRequest(BaseModel):
    """Password reset request schema."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation schema."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    """Change password request schema."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class UpdateProfileRequest(BaseModel):
    """Update profile request schema."""

    first_name: SanitizedStr | None = Field(None, min_length=1, max_length=100)
    last_name: SanitizedStr | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=20)
    avatar_url: str | None = Field(None, max_length=500)
