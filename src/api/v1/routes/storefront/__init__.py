"""Storefront API routes (customer-facing).

Public routes:
- /storefront/store/{store_id}/products - Public product catalog
- /storefront/store/{store_id}/categories - Public category listing
- /storefront/store/{store_id}/auth - Customer authentication
- /storefront/store/{store_id}/checkout - Checkout
- /storefront/store/{store_id}/products/{product_id}/bundles - FBT bundles

Authenticated customer routes:
- /storefront/me/profile - Customer profile management
- /storefront/me/password - Password change
- /storefront/me/addresses - Address management
- /storefront/me/cart - Cart management
- /storefront/me/orders - Order history
"""

from src.api.v1.routes.storefront.apps import router as apps_router
from src.api.v1.routes.storefront.bundles import router as bundles_router
from src.api.v1.routes.storefront.cart import router as cart_router
from src.api.v1.routes.storefront.checkout import router as checkout_router
from src.api.v1.routes.storefront.checkout_config import (
    router as checkout_config_router,
)
from src.api.v1.routes.storefront.coupon import router as coupon_router
from src.api.v1.routes.storefront.currencies import router as currencies_router
from src.api.v1.routes.storefront.customer import router as customer_router
from src.api.v1.routes.storefront.data_rights import router as data_rights_router
from src.api.v1.routes.storefront.geocode import router as geocode_router
from src.api.v1.routes.storefront.locations import (
    router as pickup_locations_router,
)
from src.api.v1.routes.storefront.order_tracking import router as order_tracking_router
from src.api.v1.routes.storefront.otp import router as otp_router
from src.api.v1.routes.storefront.payment_proofs import (
    router as payment_proofs_router,
)
from src.api.v1.routes.storefront.public import (
    lookup_router as storefront_lookup_router,
)
from src.api.v1.routes.storefront.public import router as public_router
from src.api.v1.routes.storefront.returns import router as returns_router
from src.api.v1.routes.storefront.reviews import router as reviews_router
from src.api.v1.routes.storefront.saved_cards import router as saved_cards_router
from src.api.v1.routes.storefront.search import router as search_router
from src.api.v1.routes.storefront.shipping import router as shipping_router
from src.api.v1.routes.storefront.shipping_quote import router as shipping_quote_router
from src.api.v1.routes.storefront.theme_resolution import (
    router as theme_resolution_router,
)
from src.api.v1.routes.storefront.tracking import router as tracking_router
from src.api.v1.routes.storefront.upsell import router as upsell_router
from src.api.v1.routes.storefront.wishlist import router as wishlist_router

__all__ = [
    "apps_router",
    "currencies_router",
    "pickup_locations_router",
    "public_router",
    "returns_router",
    "reviews_router",
    "saved_cards_router",
    "search_router",
    "wishlist_router",
    "data_rights_router",
    "storefront_lookup_router",
    "customer_router",
    "cart_router",
    "checkout_router",
    "coupon_router",
    "upsell_router",
    "otp_router",
    "payment_proofs_router",
    "tracking_router",
    "order_tracking_router",
    "theme_resolution_router",
    "shipping_quote_router",
    "shipping_router",
    "checkout_config_router",
    "bundles_router",
    "geocode_router",
]
