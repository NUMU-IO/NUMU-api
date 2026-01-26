"""Storefront API routes (customer-facing).

Public routes:
- /storefront/store/{store_id}/products - Public product catalog
- /storefront/store/{store_id}/categories - Public category listing
- /storefront/store/{store_id}/auth - Customer authentication

Authenticated customer routes:
- /storefront/me/profile - Customer profile management
- /storefront/me/password - Password change
- /storefront/me/addresses - Address management
"""

from src.api.v1.routes.storefront.public import router as public_router
from src.api.v1.routes.storefront.customer import router as customer_router

__all__ = ["public_router", "customer_router"]
