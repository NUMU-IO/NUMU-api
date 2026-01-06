"""API v1 schemas module."""

from src.api.v1.schemas.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from src.api.v1.schemas.common import (
    DeleteResponse,
    MessageResponse,
    PaginatedListResponse,
    PaginationParams,
)
from src.api.v1.schemas.product import (
    CreateProductRequest,
    ProductResponse,
    UpdateProductRequest,
)
from src.api.v1.schemas.store import (
    CreateStoreRequest,
    StoreResponse,
    UpdateStoreRequest,
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
    # Store
    "CreateStoreRequest",
    "UpdateStoreRequest",
    "StoreResponse",
    # Product
    "CreateProductRequest",
    "UpdateProductRequest",
    "ProductResponse",
    # Common
    "PaginationParams",
    "PaginatedListResponse",
    "MessageResponse",
    "DeleteResponse",
]
