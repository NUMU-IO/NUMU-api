"""Database models module."""

from src.infrastructure.database.models.base import TenantMixin, TimestampMixin, UUIDMixin

# Public schema models
from src.infrastructure.database.models.public import TenantModel, UserModel
from src.infrastructure.database.models.audit import AuditLogModel

# Tenant-scoped models (with tenant_id discriminator)
from src.infrastructure.database.models.tenant import (
    CartItemModel,
    CartModel,
    CategoryModel,
    CustomerAddressModel,
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
    "AuditLogModel",
    # Tenant-scoped
    "CartItemModel",
    "CartModel",
    "StoreModel",
    "ProductModel",
    "CategoryModel",
    "CustomerModel",
    "CustomerAddressModel",
    "OrderModel",
]

