"""API v1 routes module."""

from fastapi import APIRouter

# Public routes (no tenant context required)
from src.api.v1.routes.public import (
    auth_router,
    health_router,
    tenants_admin_router,
    tenants_router,
)

# Tenant-scoped routes (require tenant context)
from src.api.v1.routes.tenant import products_router, stores_router

# Main v1 router
api_router = APIRouter(prefix="/api/v1")

# Public routes (accessible without tenant subdomain)
api_router.include_router(health_router, prefix="/public", tags=["public"])
api_router.include_router(auth_router, prefix="/public", tags=["public"])
api_router.include_router(tenants_router, tags=["public"])
api_router.include_router(tenants_admin_router, tags=["admin"])

# Tenant routes (require subdomain)
api_router.include_router(stores_router, tags=["tenant"])
api_router.include_router(products_router, tags=["tenant"])

__all__ = ["api_router"]
