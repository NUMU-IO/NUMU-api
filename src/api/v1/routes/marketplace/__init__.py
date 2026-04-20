"""Marketplace API routes package.

Exports routers for:
- Public catalog browsing
- Developer theme submission
- Admin moderation/review
- Store installation of marketplace themes
"""

from src.api.v1.routes.marketplace.admin_review import router as marketplace_admin_router
from src.api.v1.routes.marketplace.catalog import router as marketplace_catalog_router
from src.api.v1.routes.marketplace.developer import router as marketplace_developer_router
from src.api.v1.routes.marketplace.store_install import router as marketplace_store_install_router

__all__ = [
    "marketplace_admin_router",
    "marketplace_catalog_router",
    "marketplace_developer_router",
    "marketplace_store_install_router",
]
