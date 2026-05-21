"""Configuration request API routes.

This module provides endpoints for:
- Merchants to request credential configuration
- Merchants to check configuration status
- Admins to configure credentials
- Admins to validate credentials
"""

from .admin_routes import router as admin_router

try:
    from .merchant_routes import router as merchant_router
except ImportError:
    merchant_router = None  # merchant_routes has unresolved dependencies

__all__ = ["merchant_router", "admin_router"]
