"""Authentication DTOs."""

from dataclasses import dataclass

from src.application.dto.base import BaseDTO
from src.application.dto.user import UserDTO


@dataclass
class LoginDTO(BaseDTO):
    """Login request data."""

    email: str
    password: str


@dataclass
class TokenDTO(BaseDTO):
    """Token response data."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@dataclass
class AuthResponseDTO(BaseDTO):
    """Authentication response with user and tokens."""

    user: UserDTO
    tokens: TokenDTO


@dataclass
class RegisterDTO(BaseDTO):
    """Registration request data."""

    email: str
    password: str
    first_name: str
    last_name: str
    phone: str | None = None


@dataclass
class RefreshTokenDTO(BaseDTO):
    """Refresh token request data."""

    refresh_token: str


@dataclass
class PasswordResetRequestDTO(BaseDTO):
    """Password reset request data."""

    email: str


@dataclass
class PasswordResetDTO(BaseDTO):
    """Password reset with token data."""

    token: str
    new_password: str


@dataclass
class ChangePasswordDTO(BaseDTO):
    """Change password request data."""

    current_password: str
    new_password: str
