"""API v1 schemas module."""

# Import from public schemas
from src.api.v1.schemas.public import (
    AuthResponse,
    ChangePasswordRequest,
    CreateTenantRequest,
    DeleteResponse,
    LoginRequest,
    MessageResponse,
    PaginatedListResponse,
    PaginationParams,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TenantCreatedResponse,
    TenantResponse,
    TokenResponse,
    UpdateProfileRequest,
    UpdateTenantRequest,
    UserResponse,
)

# Import from tenant schemas
from src.api.v1.schemas.tenant import (
    CreateOrderRequest,
    CreateProductRequest,
    CreateStoreRequest,
    DeleteImageRequest,
    OrderAddressRequest,
    OrderAddressResponse,
    OrderLineItemRequest,
    OrderLineItemResponse,
    OrderListItemResponse,
    OrderResponse,
    ProductResponse,
    StoreResponse,
    UpdateOrderRequest,
    UpdateOrderStatusRequest,
    UpdateProductRequest,
    UpdateStoreRequest,
    UploadedImageResponse,
)

__all__ = [
    # Auth (public)
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
    # Tenant management (public)
    "CreateTenantRequest",
    "UpdateTenantRequest",
    "TenantResponse",
    "TenantCreatedResponse",
    # Store (tenant)
    "CreateStoreRequest",
    "UpdateStoreRequest",
    "StoreResponse",
    # Product (tenant)
    "CreateProductRequest",
    "DeleteImageRequest",
    "UpdateProductRequest",
    "ProductResponse",
    "UploadedImageResponse",
    # Order (tenant)
    "CreateOrderRequest",
    "UpdateOrderRequest",
    "UpdateOrderStatusRequest",
    "OrderResponse",
    "OrderListItemResponse",
    "OrderLineItemRequest",
    "OrderLineItemResponse",
    "OrderAddressRequest",
    "OrderAddressResponse",
    # Common
    "PaginationParams",
    "PaginatedListResponse",
    "MessageResponse",
    "DeleteResponse",
]
