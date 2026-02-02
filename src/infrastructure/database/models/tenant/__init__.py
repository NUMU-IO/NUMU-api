"""Tenant-scoped database models.

These models use tenant_id discriminator for multi-tenancy.
They include all e-commerce related data that is specific to a store.
"""

from src.infrastructure.database.models.tenant.address import CustomerAddressModel
from src.infrastructure.database.models.tenant.category import CategoryModel
from src.infrastructure.database.models.tenant.coupon import CouponModel
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.invoice import InvoiceModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.configuration import (
    ConfigurationRequest,
    ServiceCredential,
    CredentialAuditLog,
    ServiceType,
    ServiceName,
    RequestStatus,
    RequestPriority,
    AuditAction,
)

__all__ = [
    "CategoryModel",
    "CouponModel",
    "CustomerAddressModel",
    "CustomerModel",
    "InvoiceModel",
    "OrderModel",
    # Configuration models
    "ConfigurationRequest",
    "ServiceCredential",
    "CredentialAuditLog",
    "ServiceType",
    "ServiceName",
    "RequestStatus",
    "RequestPriority",
    "AuditAction",
    "ProductModel",
    "StoreModel",
]

