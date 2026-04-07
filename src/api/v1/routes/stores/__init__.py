"""Store management routes.

Provides REST endpoints for store CRUD operations and nested resources:
- /stores - Store CRUD
- /stores/{store_id}/products - Product management
- /stores/{store_id}/orders - Order management
- /stores/{store_id}/dashboard - Dashboard statistics
- /stores/{store_id}/customers - Customer listing for store owners
- /stores/{store_id}/invoices - Invoice management (ETA e-invoicing)
- /stores/{store_id}/inventory - Inventory management
- /stores/{store_id}/analytics - Analytics and reporting
- /stores/{store_id}/settings - Store settings (payment, shipping, whatsapp)
- /stores/{store_id}/categories - Category management
- /stores/{store_id}/onboarding - Merchant onboarding progress
- /stores/{store_id}/webhooks - Outgoing webhook subscriptions
- /stores/{store_id}/upsells - Post-purchase upsell rules
- /stores/{store_id}/social - Social media import
- /stores/{store_id}/ai - AI description generator
"""

from fastapi import APIRouter

from src.api.v1.routes.stores import ai as ai_module
from src.api.v1.routes.stores import analytics as analytics_module
from src.api.v1.routes.stores import analytics_realtime as analytics_realtime_module
from src.api.v1.routes.stores import categories as categories_module
from src.api.v1.routes.stores import coupons as coupons_module
from src.api.v1.routes.stores import customers as customers_module
from src.api.v1.routes.stores import dashboard as dashboard_module
from src.api.v1.routes.stores import feedback as feedback_module
from src.api.v1.routes.stores import inventory as inventory_module
from src.api.v1.routes.stores import invoices as invoices_module
from src.api.v1.routes.stores import onboarding as onboarding_module
from src.api.v1.routes.stores import orders as orders_module

# Import all routers
from src.api.v1.routes.stores import payments as payments_module
from src.api.v1.routes.stores import plan as plan_module
from src.api.v1.routes.stores import products as products_module
from src.api.v1.routes.stores import reconciliation as reconciliation_module
from src.api.v1.routes.stores import refunds as refunds_module
from src.api.v1.routes.stores import settings as settings_module
from src.api.v1.routes.stores import shipments as shipments_module
from src.api.v1.routes.stores import social as social_module
from src.api.v1.routes.stores import stores as stores_module
from src.api.v1.routes.stores import themes as themes_module
from src.api.v1.routes.stores import upsells as upsells_module
from src.api.v1.routes.stores import webhooks as webhooks_module

# Create main stores router - this will be mounted at /stores in the main router
router = APIRouter()

# Store CRUD operations (mounted at root of /stores)
# Use prefix="" but routes have their own paths ("", "/{store_id}", etc.)
router.include_router(stores_module.router, prefix="", tags=["Stores"])

# Nested resources - products, orders, dashboard, customers, invoices under specific store
router.include_router(products_module.router, tags=["Store Products"])
router.include_router(orders_module.router, tags=["Store Orders"])
router.include_router(dashboard_module.router, tags=["Store Dashboard"])
router.include_router(customers_module.router, tags=["Store Customers"])
router.include_router(invoices_module.router, tags=["Store Invoices"])
router.include_router(inventory_module.router, tags=["Store Inventory"])
router.include_router(analytics_module.router, tags=["Store Analytics"])
router.include_router(
    analytics_realtime_module.router, tags=["Store Analytics Realtime"]
)
router.include_router(categories_module.router, tags=["Store Categories"])
router.include_router(coupons_module.router, tags=["Store Coupons"])
router.include_router(settings_module.router, tags=["Store Settings"])
router.include_router(onboarding_module.router, tags=["Store Onboarding"])
router.include_router(feedback_module.router, tags=["Store Feedback"])
router.include_router(refunds_module.router, tags=["Store Refunds"])
router.include_router(webhooks_module.router, tags=["Store Webhooks"])
router.include_router(reconciliation_module.router, tags=["Store Reconciliation"])
router.include_router(shipments_module.router, tags=["Store Shipments"])
router.include_router(payments_module.router, tags=["Store Payments"])
router.include_router(plan_module.router, tags=["Store Plan"])
router.include_router(upsells_module.router, tags=["Store Upsells"])
router.include_router(social_module.router, tags=["Store Social Import"])
router.include_router(ai_module.router, tags=["Store AI"])
router.include_router(themes_module.router, tags=["Store Themes"])

__all__ = ["router"]
