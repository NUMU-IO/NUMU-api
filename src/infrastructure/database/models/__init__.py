"""Database models module."""

from src.infrastructure.database.models.audit import AuditLogModel
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)

# Public schema models
from src.infrastructure.database.models.public import (
    StoreOnboardingModel,
    TenantModel,
    UserModel,
)

# Tenant-scoped models (with tenant_id discriminator)
from src.infrastructure.database.models.tenant import (
    CategoryModel,
    CouponModel,
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
    "StoreOnboardingModel",
    "TenantModel",
    "UserModel",
    "AuditLogModel",
    # Tenant-scoped
    "StoreModel",
    "ProductModel",
    "CategoryModel",
    "CouponModel",
    "CustomerModel",
    "CustomerAddressModel",
    "OrderModel",
]
