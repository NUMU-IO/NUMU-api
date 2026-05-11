"""Unit tests for authentication schemas."""

import pytest
from pydantic import ValidationError

from src.api.v1.schemas.public.auth import (
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)


class TestRegisterRequest:
    """Tests for RegisterRequest schema."""

    def test_valid_registration(self):
        """Test valid registration data."""
        data = {
            "email": "user@example.com",
            "password": "SecurePass123!",
            "first_name": "John",
            "last_name": "Doe",
        }
        request = RegisterRequest(**data)

        assert request.email == "user@example.com"
        assert request.password == "SecurePass123!"
        assert request.first_name == "John"
        assert request.last_name == "Doe"
        assert request.phone is None

    def test_valid_registration_with_phone(self):
        """Test registration with optional phone."""
        data = {
            "email": "user@example.com",
            "password": "SecurePass123!",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+201234567890",
        }
        request = RegisterRequest(**data)

        assert request.phone == "+201234567890"

    def test_invalid_email(self):
        """Test validation fails for invalid email."""
        data = {
            "email": "not-an-email",
            "password": "SecurePass123!",
            "first_name": "John",
            "last_name": "Doe",
        }
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**data)

        assert "email" in str(exc_info.value).lower()

    def test_password_too_short(self):
        """Test validation fails for short password."""
        data = {
            "email": "user@example.com",
            "password": "short",  # Less than 8 characters
            "first_name": "John",
            "last_name": "Doe",
        }
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**data)

        assert "password" in str(exc_info.value).lower()

    def test_password_too_long(self):
        """Test validation fails for too long password."""
        data = {
            "email": "user@example.com",
            "password": "a" * 129,  # Exceeds 128 character limit
            "first_name": "John",
            "last_name": "Doe",
        }
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**data)

        assert "password" in str(exc_info.value).lower()

    def test_empty_first_name(self):
        """Test validation fails for empty first name."""
        data = {
            "email": "user@example.com",
            "password": "SecurePass123!",
            "first_name": "",
            "last_name": "Doe",
        }
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**data)

        assert "first_name" in str(exc_info.value).lower()

    def test_first_name_too_long(self):
        """Test validation fails for too long first name."""
        data = {
            "email": "user@example.com",
            "password": "SecurePass123!",
            "first_name": "A" * 101,  # Exceeds 100 character limit
            "last_name": "Doe",
        }
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**data)

        assert "first_name" in str(exc_info.value).lower()

    def test_missing_required_fields(self):
        """Test validation fails for missing required fields."""
        data = {
            "email": "user@example.com",
        }
        with pytest.raises(ValidationError):
            RegisterRequest(**data)


class TestLoginRequest:
    """Tests for LoginRequest schema."""

    def test_valid_login(self):
        """Test valid login data."""
        data = {
            "email": "user@example.com",
            "password": "password123",
        }
        request = LoginRequest(**data)

        assert request.email == "user@example.com"
        assert request.password == "password123"

    def test_invalid_email(self):
        """Test validation fails for invalid email."""
        data = {
            "email": "invalid-email",
            "password": "password123",
        }
        with pytest.raises(ValidationError):
            LoginRequest(**data)

    def test_missing_password(self):
        """Test validation fails for missing password."""
        data = {
            "email": "user@example.com",
        }
        with pytest.raises(ValidationError):
            LoginRequest(**data)


class TestTokenResponse:
    """Tests for TokenResponse schema."""

    def test_valid_token_response(self):
        """Test valid token response."""
        data = {
            "access_token": "eyJ0eXAiOiJKV1...",
            "refresh_token": "eyJ0eXAiOiJSRF...",
        }
        response = TokenResponse(**data)

        assert response.access_token == "eyJ0eXAiOiJKV1..."
        assert response.refresh_token == "eyJ0eXAiOiJSRF..."
        assert response.token_type == "bearer"  # Default value

    def test_custom_token_type(self):
        """Test token response with custom token type."""
        data = {
            "access_token": "token",
            "refresh_token": "refresh",
            "token_type": "Bearer",
        }
        response = TokenResponse(**data)

        assert response.token_type == "Bearer"


class TestUserResponse:
    """Tests for UserResponse schema."""

    def test_valid_user_response(self):
        """Test valid user response."""
        data = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "email": "user@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "full_name": "John Doe",
            "role": "store_owner",
            "status": "active",
            "phone": "+201234567890",
            "avatar_url": "https://example.com/avatar.jpg",
            "is_verified": True,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        response = UserResponse(**data)

        assert response.email == "user@example.com"
        assert response.full_name == "John Doe"
        assert response.is_verified is True

    def test_user_response_optional_fields(self):
        """Test user response with optional fields as None."""
        data = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "email": "user@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "full_name": "John Doe",
            "role": "store_owner",
            "status": "active",
            "phone": None,
            "avatar_url": None,
            "is_verified": False,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        response = UserResponse(**data)

        assert response.phone is None
        assert response.avatar_url is None


class TestPasswordResetRequest:
    """Tests for PasswordResetRequest schema."""

    def test_valid_password_reset_request(self):
        """Test valid password reset request."""
        data = {
            "email": "user@example.com",
        }
        request = PasswordResetRequest(**data)

        assert request.email == "user@example.com"

    def test_invalid_email(self):
        """Test validation fails for invalid email."""
        data = {
            "email": "not-an-email",
        }
        with pytest.raises(ValidationError):
            PasswordResetRequest(**data)


class TestPasswordResetConfirm:
    """Tests for PasswordResetConfirm schema."""

    def test_valid_password_reset_confirm(self):
        """Test valid password reset confirmation."""
        data = {
            "token": "reset-token-123",
            "new_password": "NewSecurePass123!",
        }
        request = PasswordResetConfirm(**data)

        assert request.token == "reset-token-123"
        assert request.new_password == "NewSecurePass123!"

    def test_password_too_short(self):
        """Test validation fails for short new password."""
        data = {
            "token": "reset-token-123",
            "new_password": "short",
        }
        with pytest.raises(ValidationError):
            PasswordResetConfirm(**data)


class TestChangePasswordRequest:
    """Tests for ChangePasswordRequest schema."""

    def test_valid_change_password(self):
        """Test valid change password request."""
        data = {
            "current_password": "OldPassword123!",
            "new_password": "NewPassword456!",
        }
        request = ChangePasswordRequest(**data)

        assert request.current_password == "OldPassword123!"
        assert request.new_password == "NewPassword456!"

    def test_new_password_too_short(self):
        """Test validation fails for short new password."""
        data = {
            "current_password": "OldPassword123!",
            "new_password": "short",
        }
        with pytest.raises(ValidationError):
            ChangePasswordRequest(**data)
