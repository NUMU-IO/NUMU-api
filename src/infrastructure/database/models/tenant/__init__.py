"""Tenant-scoped database models.

These models use tenant_id discriminator for multi-tenancy.
They include all e-commerce related data that is specific to a store.
"""

from src.infrastructure.database.models.tenant.address import CustomerAddressModel
from src.infrastructure.database.models.tenant.cart import CartItemModel, CartModel
from src.infrastructure.database.models.tenant.category import CategoryModel
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.invoice import InvoiceModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.store import StoreModel

__all__ = [
    "CartItemModel",
    "CartModel",
    "CategoryModel",
    "CustomerAddressModel",
    "CustomerModel",
    "InvoiceModel",
    "OrderModel",
    "ProductModel",
    "StoreModel",
]

