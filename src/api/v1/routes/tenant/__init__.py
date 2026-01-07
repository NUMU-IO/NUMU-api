"""Tenant-scoped API routes (require tenant context).

These routes require a valid tenant subdomain to be accessed.
They operate within the tenant's database schema.
"""

from src.api.v1.routes.tenant.products import router as products_router
from src.api.v1.routes.tenant.stores import router as stores_router

__all__ = [
    "products_router",
    "stores_router",
]
