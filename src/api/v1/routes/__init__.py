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

# Public routes
from src.api.v1.routes.health import router as health_router
from src.api.v1.routes.auth import router as auth_router

# Tenant management routes
from src.api.v1.routes.tenants import (
    router as tenants_router,
    admin_router as tenants_admin_router,
)

# Store management routes (for store owners)
from src.api.v1.routes.stores import router as stores_router

# Storefront routes (customer-facing)
from src.api.v1.routes.storefront import (
    public_router as storefront_public_router,
    storefront_lookup_router,
    customer_router as storefront_customer_router,
    cart_router as storefront_cart_router,
    checkout_router as storefront_checkout_router,
    coupon_router as storefront_coupon_router,
)

# Webhook routes (external service callbacks)
from src.api.v1.routes.webhooks import router as webhooks_router

# Main v1 router
api_router = APIRouter(prefix="/api/v1")

# Health check (root level for easy monitoring)
api_router.include_router(health_router, tags=["Health"])

# User authentication (platform users, not customers)
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])

# Tenant management
api_router.include_router(tenants_router, prefix="/tenants", tags=["Tenants"])

# Admin routes (super admin only)
api_router.include_router(tenants_admin_router, prefix="/admin/tenants", tags=["Admin - Tenants"])

# Store management (for authenticated store owners)
api_router.include_router(stores_router, prefix="/stores", tags=["Stores"])

# Storefront - store lookup by subdomain (no store_id needed)
api_router.include_router(
    storefront_lookup_router,
    prefix="/storefront",
    tags=["Storefront - Public"],
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

# Storefront - checkout (authenticated customer, scoped to store)
api_router.include_router(
    storefront_checkout_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Checkout"],
)

# Storefront - coupon validation (authenticated customer, scoped to store)
api_router.include_router(
    storefront_coupon_router,
    prefix="/storefront/store/{store_id}",
    tags=["Storefront - Coupons"],
)

# Webhooks - external service callbacks (no auth required)
api_router.include_router(webhooks_router, prefix="/webhooks")

__all__ = ["api_router"]
