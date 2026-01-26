"""Store management routes.

Provides REST endpoints for store CRUD operations and nested resources:
- /stores - Store CRUD
- /stores/{store_id}/products - Product management
- /stores/{store_id}/customers - Customer listing for store owners
"""

from fastapi import APIRouter

# Import all routers
from src.api.v1.routes.stores import stores as stores_module
from src.api.v1.routes.stores import products as products_module
from src.api.v1.routes.stores import customers as customers_module

# Create main stores router - this will be mounted at /stores in the main router
router = APIRouter()

# Store CRUD operations (mounted at root of /stores)
# Use prefix="" but routes have their own paths ("", "/{store_id}", etc.)
router.include_router(stores_module.router, prefix="", tags=["Stores"])

# Nested resources - products and customers under specific store
router.include_router(products_module.router, tags=["Store Products"])
router.include_router(customers_module.router, tags=["Store Customers"])

__all__ = ["router"]
