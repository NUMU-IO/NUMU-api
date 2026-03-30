"""Storefront API routes (customer-facing).

Public routes:
- /storefront/store/{store_id}/products - Public product catalog
- /storefront/store/{store_id}/categories - Public category listing
- /storefront/store/{store_id}/auth - Customer authentication
- /storefront/store/{store_id}/checkout - Checkout

Authenticated customer routes:
- /storefront/me/profile - Customer profile management
- /storefront/me/password - Password change
- /storefront/me/addresses - Address management
- /storefront/me/cart - Cart management
- /storefront/me/orders - Order history
"""

from src.api.v1.routes.storefront.cart import router as cart_router
from src.api.v1.routes.storefront.checkout import router as checkout_router
from src.api.v1.routes.storefront.coupon import router as coupon_router
from src.api.v1.routes.storefront.customer import router as customer_router
from src.api.v1.routes.storefront.otp import router as otp_router
from src.api.v1.routes.storefront.public import (
    lookup_router as storefront_lookup_router,
)
from src.api.v1.routes.storefront.public import router as public_router
from src.api.v1.routes.storefront.upsell import router as upsell_router

__all__ = [
    "public_router",
    "storefront_lookup_router",
    "customer_router",
    "cart_router",
    "checkout_router",
    "coupon_router",
    "upsell_router",
    "otp_router",
]
