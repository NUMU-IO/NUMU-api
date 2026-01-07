"""Public API routes (no tenant context required).

These routes are accessible without a tenant subdomain:
- Authentication (login, register, refresh)
- Health checks
- Tenant registration/management
"""

from src.api.v1.routes.public.auth import router as auth_router
from src.api.v1.routes.public.health import router as health_router
from src.api.v1.routes.public.tenants import (
    admin_router as tenants_admin_router,
    router as tenants_router,
)

__all__ = [
    "auth_router",
    "health_router",
    "tenants_router",
    "tenants_admin_router",
]
