"""Database models module."""

from src.infrastructure.database.models.base import TenantMixin, TimestampMixin, UUIDMixin

# Public schema models
from src.infrastructure.database.models.public import TenantModel, UserModel

# Tenant-scoped models (with tenant_id discriminator)
from src.infrastructure.database.models.tenant import (
    CategoryModel,
    CustomerModel,
    OrderModel,
    ProductModel,
    StoreModel,
)

__all__ = [
    "TimestampMixin",
    "UUIDMixin",
    "TenantMixin",
    # Public
    "TenantModel",
    "UserModel",
    # Tenant-scoped
    "StoreModel",
    "ProductModel",
    "CategoryModel",
    "CustomerModel",
    "OrderModel",
]
