"""Admin routes (SUPER_ADMIN only).

URL: /api/v1/admin/
- /waitlist        — Waitlist management
- /feedback        — Feedback aggregation
- /orders          — Platform-wide order management
- /customers       — Platform-wide customer listing
- /dashboard       — Dashboard statistics
- /products        — Platform-wide product listing
- /stores          — Store lifecycle management
- /landing-config  — Landing page section visibility
- /reconciliation  — Payment reconciliation runs and mismatches
"""

from fastapi import APIRouter

from src.api.v1.routes.admin.analytics_rollups import (
    router as analytics_rollups_router,
)
from src.api.v1.routes.admin.auth import router as admin_auth_router
from src.api.v1.routes.admin.customers import router as customers_router
from src.api.v1.routes.admin.dashboard import router as dashboard_router
from src.api.v1.routes.admin.demos import router as demos_router
from src.api.v1.routes.admin.feedback import router as feedback_router
from src.api.v1.routes.admin.landing_page import router as landing_page_router
from src.api.v1.routes.admin.merchant_hub_nav import (
    router as merchant_hub_nav_router,
)
from src.api.v1.routes.admin.orders import router as orders_router
from src.api.v1.routes.admin.platform_config import router as platform_config_router
from src.api.v1.routes.admin.platform_settings import (
    router as platform_settings_router,
)
from src.api.v1.routes.admin.products import router as products_router
from src.api.v1.routes.admin.reconciliation import router as reconciliation_router
from src.api.v1.routes.admin.stores import router as stores_router
from src.api.v1.routes.admin.users import router as admin_users_router
from src.api.v1.routes.admin.waitlist import router as waitlist_router
from src.api.v1.routes.tenant.configuration.admin_routes import (
    router as credentials_router,
)

router = APIRouter()

router.include_router(waitlist_router, prefix="/waitlist", tags=["Admin - Waitlist"])
router.include_router(demos_router, prefix="/demos", tags=["Admin - Demos"])
router.include_router(feedback_router, prefix="/feedback", tags=["Admin - Feedback"])
router.include_router(orders_router, prefix="/orders", tags=["Admin - Orders"])
router.include_router(customers_router, prefix="/customers", tags=["Admin - Customers"])
router.include_router(dashboard_router, prefix="/dashboard", tags=["Admin - Dashboard"])
router.include_router(products_router, prefix="/products", tags=["Admin - Products"])
router.include_router(stores_router, prefix="/stores", tags=["Admin - Stores"])
router.include_router(
    landing_page_router, prefix="/landing-config", tags=["Admin - Landing Page"]
)
router.include_router(
    reconciliation_router, prefix="/reconciliation", tags=["Admin - Reconciliation"]
)
router.include_router(
    platform_config_router,
    prefix="/platform-config",
    tags=["Admin - Platform Config"],
)
router.include_router(
    platform_settings_router,
    prefix="/platform-settings",
    tags=["Admin - Platform Settings"],
)
router.include_router(
    merchant_hub_nav_router,
    prefix="/merchant-hub-nav",
    tags=["Admin - Merchant Hub Nav"],
)
router.include_router(
    admin_users_router,
    prefix="/users",
    tags=["Admin - Users"],
)
router.include_router(
    admin_auth_router,
    prefix="/auth",
    tags=["Admin - Auth"],
)
router.include_router(
    analytics_rollups_router,
    prefix="/analytics-rollups",
    tags=["Admin - Analytics Rollups"],
)
# Credentials router already has prefix="/admin/credentials" built-in,
# so we include it at root "" to avoid /admin/admin/credentials
router.include_router(credentials_router)

__all__ = ["router"]
