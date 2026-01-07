"""API v1 routes module."""

from fastapi import APIRouter

from src.api.v1.routes.auth import router as auth_router
from src.api.v1.routes.health import router as health_router
from src.api.v1.routes.products import router as products_router
from src.api.v1.routes.stores import router as stores_router
from src.api.v1.routes.tenants import router as tenants_router
from src.api.v1.routes.tenants import admin_router as tenants_admin_router

# Main v1 router
api_router = APIRouter(prefix="/api/v1")

# Include all routers
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(stores_router)
api_router.include_router(products_router)
api_router.include_router(tenants_router)
api_router.include_router(tenants_admin_router)

__all__ = ["api_router"]
