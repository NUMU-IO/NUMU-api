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
from src.api.v1.schemas.public.two_factor import (
    Complete2FALoginRequest,
    Disable2FARequest,
    Enable2FAResponse,
    RegenerateBackupCodesRequest,
    RegenerateBackupCodesResponse,
    TwoFactorChallengeResponse,
    TwoFactorStatusResponse,
    Verify2FARequest,
    Verify2FAResponse,
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
    # 2FA
    "Enable2FAResponse",
    "Verify2FARequest",
    "Verify2FAResponse",
    "Disable2FARequest",
    "TwoFactorStatusResponse",
    "RegenerateBackupCodesRequest",
    "RegenerateBackupCodesResponse",
    "TwoFactorChallengeResponse",
    "Complete2FALoginRequest",
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
