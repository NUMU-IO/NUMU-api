"""Database models module."""

from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin

# Public schema models
from src.infrastructure.database.models.public import Tenant, UserModel

# Tenant schema models
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
    # Public
    "Tenant",
    "UserModel",
    # Tenant
    "StoreModel",
    "ProductModel",
    "CategoryModel",
    "CustomerModel",
    "OrderModel",
]
