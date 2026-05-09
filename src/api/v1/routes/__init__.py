"""API v1 routes module.

URL Hierarchy:
/api/v1/
├── health/                    # Health checks
├── auth/                      # User authentication
│   ├── POST /register
│   ├── POST /login
│   ├── POST /refresh
│   └── GET /me
├── admin/                     # Super admin only
│   └── /tenants/...
├── tenants/                   # Tenant registration (authenticated users)
│   ├── POST /
│   └── GET /check-subdomain/{subdomain}
├── stores/                    # Store owner operations
│   ├── GET/POST /
│   ├── GET/PATCH/DELETE /{store_id}
│   ├── /{store_id}/products/...
│   └── /{store_id}/customers/...
├── storefront/                # Customer-facing
│   ├── /store/{store_id}/     # Public catalog & auth
│   │   ├── GET /products
│   │   ├── GET /categories
│   │   └── /auth/...          # Customer auth
│   └── /me/                   # Authenticated customer
│       ├── GET/PUT /profile
│       ├── PUT /password
│       └── /addresses/...
└── webhooks/                  # External service callbacks
    ├── /paymob/               # Paymob payment notifications
    └── /fawry/                # Fawry payment notifications
"""

from fastapi import APIRouter

# Admin routes (super admin only — waitlist, feedback)
from src.api.v1.routes.admin import router as admin_router
from src.api.v1.routes.auth import router as auth_router

# Billing routes (subscribe, cancel, invoices)
from src.api.v1.routes.billing import router as billing_router

# Demo routes (authenticated demo session)
from src.api.v1.routes.demo import router as demo_router

# Public routes (no auth)
from src.api.v1.routes.health import router as health_router

# Marketplace V3 routes (catalog, developer, admin, store install)
from src.api.v1.routes.marketplace import (
    marketplace_admin_router,
    marketplace_catalog_router,
    marketplace_developer_router,
    marketplace_store_install_router,
)

# Omnichannel routes
from src.api.v1.routes.omnichannel import (
    capi_router,
    catalog_router,
    channels_router,
    messages_router,
    templates_router,
    threads_router,
)
from src.api.v1.routes.permissions.routes import router as permissions_router
from src.api.v1.routes.public import router as public_router

# Referral routes (merchant-to-merchant referral program)
from src.api.v1.routes.referrals import router as referrals_router
from src.api.v1.routes.roles.routes import router as roles_router

# Shopify app routes
from src.api.v1.routes.shopify import router as shopify_router
from src.api.v1.routes.staff.access_requests import (
    router as staff_access_requests_router,
)

# Staff & roles routes
from src.api.v1.routes.staff.invitations import router as staff_invitations_router
from src.api.v1.routes.staff.list import router as staff_list_router
from src.api.v1.routes.staff.overrides import router as staff_overrides_router
from src.api.v1.routes.staff.policies import router as staff_policies_router
from src.api.v1.routes.staff.sessions import router as staff_sessions_router
from src.api.v1.routes.storefront import (
    apps_router as storefront_apps_router,
)
from src.api.v1.routes.storefront import (
    bundles_router as storefront_bundles_router,
)
from src.api.v1.routes.storefront import (
    cart_router as storefront_cart_router,
)
from src.api.v1.routes.storefront import (
    checkout_config_router as storefront_checkout_config_router,
)
from src.api.v1.routes.storefront import (
    checkout_router as storefront_checkout_router,
)
from src.api.v1.routes.storefront import (
    coupon_router as storefront_coupon_router,
)
from src.api.v1.routes.storefront import (
    currencies_router as storefront_currencies_router,
)
from src.api.v1.routes.storefront import (
    customer_router as storefront_customer_router,
)
from src.api.v1.routes.storefront import (
    geocode_router as storefront_geocode_router,
)
from src.api.v1.routes.storefront import (
    order_tracking_router as storefront_order_tracking_router,
)
from src.api.v1.routes.storefront import (
    otp_router as storefront_otp_router,
)
from src.api.v1.routes.storefront import (
    payment_proofs_router as storefront_payment_proofs_router,
)
from src.api.v1.routes.storefront import (
    promotions_router as storefront_promotions_router,
)

# Storefront routes (customer-facing)
from src.api.v1.routes.storefront import (
    public_router as storefront_public_router,
)
from src.api.v1.routes.storefront import (
    reviews_router as storefront_reviews_router,
)
from src.api.v1.routes.storefront import (
    shipping_quote_router as storefront_shipping_quote_router,
)
from src.api.v1.routes.storefront import (
    shipping_router as storefront_shipping_router,
)

# Storefront theme resolution (internal — Next.js SSR → FastAPI)
from src.api.v1.routes.storefront import (
    storefront_lookup_router,
    theme_resolution_router,
)
from src.api.v1.routes.storefront import (
    tracking_router as storefront_tracking_router,
)
from src.api.v1.routes.storefront import (
    upsell_router as storefront_upsell_router,
)
from src.api.v1.routes.storefront.cart_sdk_aliases import (
    router as storefront_cart_sdk_router,
)

# Store management routes (for store owners)
from src.api.v1.routes.stores import router as stores_router
from src.api.v1.routes.tenants import (
    admin_router as tenants_admin_router,
)

# Tenant management routes
from src.api.v1.routes.tenants import (
    router as tenants_router,
)

# Theme marketplace routes (public)
from src.api.v1.routes.themes import router as themes_marketplace_router

# Theme ZIP upload + build status + preview token (authenticated developers)
from src.api.v1.routes.themes_upload import router as themes_upload_router

# Webhook routes (external service callbacks)
from src.api.v1.routes.webhooks import router as webhooks_router

# WebSocket routes
from src.api.v1.routes.ws import router as ws_router

# Main v1 router
api_router = APIRouter(prefix="/api/v1")

# Health check (root level for easy monitoring)
api_router.include_router(health_router, tags=["Health"])

# User authentication (platform users, not customers)
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])

# Tenant management
api_router.include_router(tenants_router, prefix="/tenants", tags=["Tenants"])

# Public routes (no auth — waitlist, landing page stats)
api_router.include_router(public_router, prefix="/public", tags=["Public"])

# Admin routes (super admin only)
api_router.include_router(
    tenants_admin_router, prefix="/admin/tenants", tags=["Admin - Tenants"]
)
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])

# Store management (for authenticated store owners)
api_router.include_router(stores_router, prefix="/stores")

# Omnichannel - inbox (channels, threads, messages under store scope)
api_router.include_router(
    channels_router,
    prefix="/stores/{store_id}/channels",
    tags=["Omnichannel - Channels"],
)
api_router.include_router(
    threads_router,
    prefix="/stores/{store_id}/threads",
    tags=["Omnichannel - Threads"],
)
api_router.include_router(
    messages_router,
    prefix="/stores/{store_id}/threads/{thread_id}/messages",
    tags=["Omnichannel - Messages"],
)
api_router.include_router(
    templates_router,
    prefix="/stores/{store_id}/whatsapp",
    tags=["Omnichannel - Templates"],
)
api_router.include_router(
    catalog_router,
    prefix="/stores/{store_id}/catalog",
    tags=["Omnichannel - Catalog"],
)
api_router.include_router(
    capi_router,
    prefix="/stores/{store_id}/capi",
    tags=["Omnichannel - CAPI"],
)

# Storefront - store lookup by subdomain (no store_id needed)
api_router.include_router(
    storefront_lookup_router,
    prefix="/storefront",
    tags=["Storefront - Public"],
)

# Storefront - public order tracking (no store_id needed; scoped by order UUID)
api_router.include_router(
    storefront_order_tracking_router,
    prefix="/storefront",
    tags=["Storefront - Tracking"],
)

# Storefront - public routes (catalog, customer auth)
api_router.include_router(
    storefront_public_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Public"],
)

# Storefront - authenticated customer routes
api_router.include_router(
    storefront_customer_router,
    prefix="/storefront/me",
    tags=["Storefront - Customer"],
)

# Storefront - cart (authenticated customer)
api_router.include_router(
    storefront_cart_router,
    prefix="/storefront/me",
    tags=["Storefront - Cart"],
)

# Storefront - cart SDK aliases (consumed by @numu/theme-sdk's NuMuProvider).
# Same auth + Redis backend as /storefront/me/cart, just at the URL layout
# the SDK posts to. Kept as a separate include so the legacy paths stay
# unchanged for older themes during the migration period.
api_router.include_router(
    storefront_cart_sdk_router,
    prefix="/storefront",
    tags=["Storefront - Cart (SDK aliases)"],
)

# Storefront - checkout (authenticated customer, scoped to store)
api_router.include_router(
    storefront_checkout_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Checkout"],
)

# Storefront - public checkout field config (no auth, scoped to store)
api_router.include_router(
    storefront_checkout_config_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Checkout"],
)

# Storefront - reverse geocoding proxy for the checkout location picker
api_router.include_router(
    storefront_geocode_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Geocoding"],
)

# Storefront - coupon validation (authenticated customer, scoped to store)
api_router.include_router(
    storefront_coupon_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Coupons"],
)

# Storefront - offers-v2 promotions (active list, events, dismiss)
api_router.include_router(
    storefront_promotions_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Promotions"],
)

# Storefront - product reviews (GET public, POST requires customer auth)
api_router.include_router(
    storefront_reviews_router,
    prefix="/storefront/store/{store_id}/products",
    tags=["Storefront - Reviews"],
)

# Storefront - upsell offers (public, scoped to store)
api_router.include_router(
    storefront_upsell_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Upsells"],
)

# Storefront - FBT bundles (public, scoped to store)
api_router.include_router(
    storefront_bundles_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Bundles"],
)

# Storefront - page view tracking (public, scoped to store)
api_router.include_router(
    storefront_tracking_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Tracking"],
)

# Storefront - COD OTP verification (authenticated customer, scoped to store)
api_router.include_router(
    storefront_otp_router,
    prefix="/storefront/store/{store_id}/checkout",
    tags=["Storefront - Checkout"],
)

# Storefront - shipping rate quotes (public, scoped to store, legacy)
api_router.include_router(
    storefront_shipping_quote_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Shipping"],
)

# Storefront - shipping governorates + options (public, scoped to store)
api_router.include_router(
    storefront_shipping_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Shipping"],
)

# Storefront - InstaPay proof upload / status (authenticated customer)
api_router.include_router(
    storefront_payment_proofs_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Payment Proofs"],
)

# Storefront - app platform discovery (Phase 6, public, scoped to store)
api_router.include_router(
    storefront_apps_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Apps"],
)

# Storefront - currency presentment config (Phase 6, public, scoped to store)
api_router.include_router(
    storefront_currencies_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Currencies"],
)

# Theme marketplace (public browsing of published themes)
api_router.include_router(themes_marketplace_router, tags=["Themes - Marketplace"])

# Marketplace V3 routes
api_router.include_router(marketplace_catalog_router, tags=["Marketplace - Catalog"])
api_router.include_router(
    marketplace_developer_router, tags=["Marketplace - Developer"]
)
api_router.include_router(marketplace_admin_router, tags=["Marketplace - Admin"])
api_router.include_router(
    marketplace_store_install_router,
    prefix="/stores/{store_id}",
    tags=["Marketplace - Store Install"],
)

# Theme upload + build status + preview (authenticated)
api_router.include_router(themes_upload_router, tags=["Themes - Upload"])

# Storefront - theme resolution (internal, Next.js SSR → FastAPI)
api_router.include_router(
    theme_resolution_router,
    prefix="/storefront",
    tags=["Storefront - Theme Resolution"],
)
# Demo routes (authenticated demo session → convert to real account)
api_router.include_router(demo_router, tags=["Demo"])

# Billing routes (subscribe, cancel, invoices, discount codes)
api_router.include_router(billing_router, tags=["Billing"])

# Referral routes (merchant referral program)
api_router.include_router(referrals_router, tags=["Referrals"])

# Shopify app integration (register-shop, lookup, dashboard, risk, payments, etc.)
api_router.include_router(shopify_router, prefix="/shopify")

# Staff management routes (prefixes defined on the routers themselves).
#
# Ordering matters: `staff_list_router` owns the catch-all
# `/staff/{membership_id}` GET/DELETE/PUT routes, so every static-prefix
# staff sub-router (/staff/overrides, /staff/sessions, /staff/policies,
# /staff/access-requests, /staff/invitations) MUST be included before it.
# Otherwise FastAPI matches `/staff/overrides` against `{membership_id}`,
# fails UUID validation on the literal string, and returns 422.
api_router.include_router(staff_invitations_router, tags=["Staff - Invitations"])
api_router.include_router(staff_overrides_router, tags=["Staff - Overrides"])
api_router.include_router(staff_sessions_router, tags=["Staff - Sessions"])
api_router.include_router(
    staff_access_requests_router, tags=["Staff - Access Requests"]
)
api_router.include_router(staff_policies_router, tags=["Staff - Policies"])
api_router.include_router(staff_list_router, tags=["Staff"])

# Role management routes
api_router.include_router(roles_router, tags=["Roles"])

# Permission catalog routes
api_router.include_router(permissions_router, tags=["Permissions"])

# Webhooks - external service callbacks (no auth required)
api_router.include_router(webhooks_router, prefix="/webhooks")

# WebSocket for realtime updates
api_router.include_router(ws_router, prefix="/ws", tags=["WebSocket"])

__all__ = ["api_router"]
