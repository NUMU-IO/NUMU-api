"""Shopify routes package.

Mounts all Shopify sub-routers under /api/v1/shopify:
  /shopify/auth/*             — register-shop, lookup
  /shopify/webhooks/*         — process
  /shopify/{storeId}/*        — dashboard, risk, payments, automation, settings
"""

from fastapi import APIRouter

from src.api.v1.routes.shopify.auth import router as auth_router
from src.api.v1.routes.shopify.automation import router as automation_router
from src.api.v1.routes.shopify.billing import router as billing_router
from src.api.v1.routes.shopify.courier_stats import router as courier_stats_router
from src.api.v1.routes.shopify.dashboard import router as dashboard_router
from src.api.v1.routes.shopify.otp import router as otp_router
from src.api.v1.routes.shopify.payment_links import (
    router_internal as payment_links_internal_router,
)
from src.api.v1.routes.shopify.payment_links import (
    router_public as payment_links_public_router,
)
from src.api.v1.routes.shopify.payments import router as payments_router
from src.api.v1.routes.shopify.recovery import router as recovery_router
from src.api.v1.routes.shopify.risk import router as risk_router
from src.api.v1.routes.shopify.settings import router as settings_router
from src.api.v1.routes.shopify.webhooks import router as webhooks_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Shopify - Auth"])
router.include_router(webhooks_router, prefix="/webhooks", tags=["Shopify - Webhooks"])
router.include_router(dashboard_router, tags=["Shopify - Dashboard"])
router.include_router(risk_router, tags=["Shopify - Risk"])
router.include_router(payments_router, tags=["Shopify - Payments"])
router.include_router(automation_router, tags=["Shopify - Automation"])
router.include_router(settings_router, tags=["Shopify - Settings"])
router.include_router(billing_router, tags=["Shopify - Billing"])
router.include_router(recovery_router, tags=["Shopify - Recovery"])
router.include_router(courier_stats_router, tags=["Shopify - Courier Stats"])
router.include_router(otp_router, tags=["Shopify - OTP"])
router.include_router(payment_links_internal_router, tags=["Shopify - Payment Links"])
# Public payment link endpoints (no auth required — customer-facing)
router.include_router(
    payment_links_public_router, tags=["Shopify - Payment Links (Public)"]
)

__all__ = ["router"]
