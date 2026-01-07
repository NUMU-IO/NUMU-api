"""Tenant-scoped API schemas (products, stores, orders)."""

from src.api.v1.schemas.tenant.common import (
    DeleteResponse,
    MessageResponse,
    PaginatedListResponse,
    PaginationParams,
)
from src.api.v1.schemas.tenant.product import (
    CreateProductRequest,
    ProductResponse,
    UpdateProductRequest,
)
from src.api.v1.schemas.tenant.store import (
    CreateStoreRequest,
    StoreResponse,
    UpdateStoreRequest,
)

__all__ = [
    # Product
    "CreateProductRequest",
    "UpdateProductRequest",
    "ProductResponse",
    # Store
    "CreateStoreRequest",
    "UpdateStoreRequest",
    "StoreResponse",
    # Common
    "PaginationParams",
    "PaginatedListResponse",
    "MessageResponse",
    "DeleteResponse",
]
