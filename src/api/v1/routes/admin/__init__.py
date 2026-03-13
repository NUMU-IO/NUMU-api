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

from src.api.v1.routes.admin.customers import router as customers_router
from src.api.v1.routes.admin.dashboard import router as dashboard_router
from src.api.v1.routes.admin.feedback import router as feedback_router
from src.api.v1.routes.admin.landing_page import router as landing_page_router
from src.api.v1.routes.admin.orders import router as orders_router
from src.api.v1.routes.admin.products import router as products_router
from src.api.v1.routes.admin.reconciliation import router as reconciliation_router
from src.api.v1.routes.admin.stores import router as stores_router
from src.api.v1.routes.admin.waitlist import router as waitlist_router

router = APIRouter()

router.include_router(waitlist_router, prefix="/waitlist", tags=["Admin - Waitlist"])
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

__all__ = ["router"]
