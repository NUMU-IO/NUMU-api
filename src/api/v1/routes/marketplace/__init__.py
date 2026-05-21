"""Marketplace API routes package.

Exports routers for:
- Public catalog browsing (`/marketplace/catalog`)
- Developer theme submission (`/marketplace/developer`, requires auth)
- Admin moderation/review (`/marketplace/admin`, requires SUPER_ADMIN)
- Store installation (`/stores/{store_id}/marketplace`, store-owner auth)

Each router declares its own auth dependencies — there are no
unauthenticated developer or admin endpoints.
"""

from src.api.v1.routes.marketplace.admin_review import (
    router as marketplace_admin_router,
)
from src.api.v1.routes.marketplace.catalog import (
    router as marketplace_catalog_router,
)
from src.api.v1.routes.marketplace.developer import (
    router as marketplace_developer_router,
)
from src.api.v1.routes.marketplace.purchases import (
    router as marketplace_purchases_router,
)
from src.api.v1.routes.marketplace.reviews import (
    router as marketplace_reviews_router,
)
from src.api.v1.routes.marketplace.store_install import (
    router as marketplace_store_install_router,
)

__all__ = [
    "marketplace_admin_router",
    "marketplace_catalog_router",
    "marketplace_developer_router",
    "marketplace_purchases_router",
    "marketplace_reviews_router",
    "marketplace_store_install_router",
]
