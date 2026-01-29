"""Public API schemas (authentication, tenant management)."""

from src.api.v1.schemas.public.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from src.api.v1.schemas.public.common import (
    DeleteResponse,
    MessageResponse,
    PaginatedListResponse,
    PaginationParams,
)
from src.api.v1.schemas.public.tenant import (
    CreateTenantRequest,
    TenantCreatedResponse,
    TenantResponse,
    UpdateTenantRequest,
)

__all__ = [
    # Auth
    "RegisterRequest",
    "LoginRequest",
    "TokenResponse",
    "UserResponse",
    "AuthResponse",
    "RefreshTokenRequest",
    "PasswordResetRequest",
    "PasswordResetConfirm",
    "ChangePasswordRequest",
    "UpdateProfileRequest",
    # Tenant
    "CreateTenantRequest",
    "UpdateTenantRequest",
    "TenantResponse",
    "TenantCreatedResponse",
    # Common
    "PaginationParams",
    "PaginatedListResponse",
    "MessageResponse",
    "DeleteResponse",
]
