"""Store management routes.

Provides REST endpoints for store CRUD operations and nested resources:
- /stores - Store CRUD
- /stores/{store_id}/products - Product management
- /stores/{store_id}/orders - Order management
- /stores/{store_id}/dashboard - Dashboard statistics
- /stores/{store_id}/customers - Customer listing for store owners
- /stores/{store_id}/invoices - Invoice management (ETA e-invoicing)
- /stores/{store_id}/inventory - Inventory management
- /stores/{store_id}/analytics - Analytics and reporting
- /stores/{store_id}/settings - Store settings (payment, shipping, whatsapp)
"""

from fastapi import APIRouter

from src.api.v1.routes.stores import analytics as analytics_module
from src.api.v1.routes.stores import coupons as coupons_module
from src.api.v1.routes.stores import customers as customers_module
from src.api.v1.routes.stores import dashboard as dashboard_module
from src.api.v1.routes.stores import inventory as inventory_module
from src.api.v1.routes.stores import invoices as invoices_module
from src.api.v1.routes.stores import orders as orders_module
from src.api.v1.routes.stores import products as products_module
from src.api.v1.routes.stores import settings as settings_module

# Import all routers
from src.api.v1.routes.stores import stores as stores_module

# Create main stores router - this will be mounted at /stores in the main router
router = APIRouter()

# Store CRUD operations (mounted at root of /stores)
# Use prefix="" but routes have their own paths ("", "/{store_id}", etc.)
router.include_router(stores_module.router, prefix="", tags=["Stores"])

# Nested resources - products, orders, dashboard, customers, invoices under specific store
router.include_router(products_module.router, tags=["Store Products"])
router.include_router(orders_module.router, tags=["Store Orders"])
router.include_router(dashboard_module.router, tags=["Store Dashboard"])
router.include_router(customers_module.router, tags=["Store Customers"])
router.include_router(invoices_module.router, tags=["Store Invoices"])
router.include_router(inventory_module.router, tags=["Store Inventory"])
router.include_router(analytics_module.router, tags=["Store Analytics"])
router.include_router(coupons_module.router, tags=["Store Coupons"])
router.include_router(settings_module.router, tags=["Store Settings"])

__all__ = ["router"]
