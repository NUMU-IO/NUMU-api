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
- /stores/{store_id}/bundles - Frequently Bought Together bundles
- /stores/{store_id}/social - Social media import
- /stores/{store_id}/ai - AI description generator
"""

from fastapi import APIRouter

from src.api.v1.routes.stores import (
    abandoned_checkouts as abandoned_checkouts_module,
)
from src.api.v1.routes.stores import ai as ai_module
from src.api.v1.routes.stores import analytics as analytics_module
from src.api.v1.routes.stores import analytics_realtime as analytics_realtime_module
from src.api.v1.routes.stores import apps as apps_module
from src.api.v1.routes.stores import bundles as bundles_module
from src.api.v1.routes.stores import categories as categories_module
from src.api.v1.routes.stores import cod_trust_decisions as cod_trust_decisions_module
from src.api.v1.routes.stores import coupons as coupons_module
from src.api.v1.routes.stores import customers as customers_module
from src.api.v1.routes.stores import customizer_undo as customizer_undo_module
from src.api.v1.routes.stores import dashboard as dashboard_module
from src.api.v1.routes.stores import email_templates as email_templates_module
from src.api.v1.routes.stores import feedback as feedback_module
from src.api.v1.routes.stores import gift_cards as gift_cards_module
from src.api.v1.routes.stores import inventory as inventory_module
from src.api.v1.routes.stores import inventory_levels as inventory_levels_module
from src.api.v1.routes.stores import inventory_transfers as inventory_transfers_module
from src.api.v1.routes.stores import invoices as invoices_module
from src.api.v1.routes.stores import locations as locations_module
from src.api.v1.routes.stores import (
    marketing_audiences as marketing_audiences_module,
)
from src.api.v1.routes.stores import (
    marketing_campaign_activities as marketing_campaign_activities_module,
)
from src.api.v1.routes.stores import (
    marketing_campaign_rules as marketing_campaign_rules_module,
)
from src.api.v1.routes.stores import (
    marketing_campaigns as marketing_campaigns_module,
)
from src.api.v1.routes.stores import (
    marketing_send_times as marketing_send_times_module,
)
from src.api.v1.routes.stores import onboarding as onboarding_module
from src.api.v1.routes.stores import order_import as order_import_module
from src.api.v1.routes.stores import orders as orders_module

# Import all routers
from src.api.v1.routes.stores import payment_proofs as payment_proofs_module
from src.api.v1.routes.stores import payments as payments_module
from src.api.v1.routes.stores import plan as plan_module
from src.api.v1.routes.stores import products as products_module
from src.api.v1.routes.stores import promotions as promotions_module
from src.api.v1.routes.stores import reconciliation as reconciliation_module
from src.api.v1.routes.stores import refunds as refunds_module
from src.api.v1.routes.stores import returns as returns_module
from src.api.v1.routes.stores import settings as settings_module
from src.api.v1.routes.stores import shipments as shipments_module
from src.api.v1.routes.stores import shipping as shipping_module
from src.api.v1.routes.stores import social as social_module
from src.api.v1.routes.stores import (
    storefront_validation as storefront_validation_module,
)
from src.api.v1.routes.stores import stores as stores_module
from src.api.v1.routes.stores import theme_editor_v3 as theme_editor_v3_module
from src.api.v1.routes.stores import theme_installations as theme_installations_module
from src.api.v1.routes.stores import themes as themes_module
from src.api.v1.routes.stores import upsells as upsells_module
from src.api.v1.routes.stores import webhooks as webhooks_module
from src.api.v1.routes.stores import whatsapp as whatsapp_module
from src.api.v1.routes.stores import whatsapp_campaigns as whatsapp_campaigns_module
from src.api.v1.routes.stores import whatsapp_chat as whatsapp_chat_module
from src.api.v1.routes.stores import (
    whatsapp_dead_letters as whatsapp_dead_letters_module,
)
from src.api.v1.routes.stores import whatsapp_opt_ins as whatsapp_opt_ins_module
from src.api.v1.routes.stores import (
    whatsapp_scheduled_sends as whatsapp_scheduled_sends_module,
)
from src.api.v1.routes.stores import whatsapp_templates as whatsapp_templates_module

# Create main stores router - this will be mounted at /stores in the main router
router = APIRouter()

# Store CRUD operations (mounted at root of /stores)
# Use prefix="" but routes have their own paths ("", "/{store_id}", etc.)
router.include_router(stores_module.router, prefix="", tags=["Stores"])

# Nested resources - products, orders, dashboard, customers, invoices under specific store
router.include_router(products_module.router, tags=["Store Products"])
# payment_proofs must be registered BEFORE orders: it owns the static
# path ``/{store_id}/orders/pending-instapay-review`` which would
# otherwise be shadowed by the orders router's catch-all
# ``/{store_id}/orders/{order_id}`` pattern (FastAPI matches routes in
# include order, so the UUID-typed wildcard wins and 422s on the
# non-UUID literal segment).
router.include_router(payment_proofs_module.router, tags=["Store InstaPay Proofs"])
router.include_router(orders_module.router, tags=["Store Orders"])
router.include_router(
    abandoned_checkouts_module.router, tags=["Store Abandoned Checkouts"]
)
router.include_router(order_import_module.router, tags=["Store Order Import"])
router.include_router(dashboard_module.router, tags=["Store Dashboard"])
router.include_router(customers_module.router, tags=["Store Customers"])
router.include_router(invoices_module.router, tags=["Store Invoices"])
router.include_router(gift_cards_module.router, tags=["Store Gift Cards"])
router.include_router(inventory_module.router, tags=["Store Inventory"])
router.include_router(inventory_levels_module.router, tags=["Inventory Levels"])
router.include_router(inventory_transfers_module.router, tags=["Inventory Transfers"])
router.include_router(locations_module.router, tags=["Store Locations"])
router.include_router(marketing_campaigns_module.router, tags=["Marketing Campaigns"])
# Feature 002 — auto-match rules, campaign activities, send-time suggestions
router.include_router(
    marketing_campaign_rules_module.router,
    tags=["Marketing Campaign Auto-Match Rules"],
)
router.include_router(
    marketing_campaign_activities_module.router,
    tags=["Marketing Campaign Activities"],
)
router.include_router(
    marketing_send_times_module.router,
    tags=["Marketing Send Times"],
)
router.include_router(
    marketing_audiences_module.router,
    tags=["Marketing Audiences"],
)
router.include_router(analytics_module.router, tags=["Store Analytics"])
router.include_router(
    analytics_realtime_module.router, tags=["Store Analytics Realtime"]
)
router.include_router(categories_module.router, tags=["Store Categories"])
router.include_router(coupons_module.router, tags=["Store Coupons"])
router.include_router(promotions_module.router, tags=["Store Promotions"])
router.include_router(settings_module.router, tags=["Store Settings"])
router.include_router(
    cod_trust_decisions_module.router, tags=["Store COD Trust Decisions"]
)
router.include_router(onboarding_module.router, tags=["Store Onboarding"])
router.include_router(feedback_module.router, tags=["Store Feedback"])
router.include_router(refunds_module.router, tags=["Store Refunds"])
router.include_router(returns_module.router, tags=["Store Returns"])
router.include_router(webhooks_module.router, tags=["Store Webhooks"])
router.include_router(reconciliation_module.router, tags=["Store Reconciliation"])
router.include_router(shipments_module.router, tags=["Store Shipments"])
router.include_router(shipping_module.router, tags=["Store Shipping"])
router.include_router(payments_module.router, tags=["Store Payments"])
router.include_router(plan_module.router, tags=["Store Plan"])
router.include_router(upsells_module.router, tags=["Store Upsells"])
router.include_router(bundles_module.router, tags=["Store Bundles"])
router.include_router(social_module.router, tags=["Store Social Import"])
router.include_router(
    storefront_validation_module.router, tags=["Storefront Validation"]
)
router.include_router(ai_module.router, tags=["Store AI"])
router.include_router(themes_module.router, tags=["Store Themes"])
router.include_router(theme_installations_module.router, tags=["Store Themes V2"])
router.include_router(theme_editor_v3_module.router, tags=["Theme Editor V3"])
router.include_router(customizer_undo_module.router, tags=["Theme Editor V3 — Undo"])
router.include_router(apps_module.router, tags=["Store Apps"])
router.include_router(whatsapp_module.router, tags=["Store WhatsApp"])
router.include_router(
    whatsapp_templates_module.router, tags=["Store WhatsApp Templates"]
)
router.include_router(email_templates_module.router, tags=["Store Email Templates"])
router.include_router(whatsapp_chat_module.router, tags=["Store WhatsApp Chat"])
router.include_router(
    whatsapp_campaigns_module.router, tags=["Store WhatsApp Campaigns"]
)
router.include_router(whatsapp_opt_ins_module.router, tags=["Store WhatsApp Opt-Ins"])
router.include_router(
    whatsapp_scheduled_sends_module.router,
    tags=["Store WhatsApp Scheduled Sends"],
)
router.include_router(
    whatsapp_dead_letters_module.router,
    tags=["Store WhatsApp Dead-Letters"],
)

__all__ = ["router"]
