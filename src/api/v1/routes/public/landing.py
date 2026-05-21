"""Public landing page endpoints — no auth required.

URL: /api/v1/public/stats, /api/v1/public/features, /api/v1/public/landing-config
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.platform_config import (
    DEFAULT_LANDING_CONFIG,
    PlatformConfigModel,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/stats",
    summary="Public platform statistics for landing page",
    operation_id="get_public_stats",
)
async def get_public_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return aggregate platform stats for the marketing landing page.

    Numbers are intentionally rounded/approximated to avoid leaking
    exact business metrics publicly.
    """
    from src.infrastructure.database.models.public.waitlist import WaitlistModel
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel

    # Active store count
    store_result = await db.execute(select(func.count(StoreModel.id)))
    store_count = store_result.scalar() or 0

    # Total orders processed
    order_result = await db.execute(select(func.count(OrderModel.id)))
    order_count = order_result.scalar() or 0

    # Waitlist count
    waitlist_result = await db.execute(select(func.count(WaitlistModel.id)))
    waitlist_count = waitlist_result.scalar() or 0

    # Round to friendly numbers for marketing
    def _round_up(n: int, nearest: int = 10) -> int:
        if n < nearest:
            return n
        return ((n // nearest) + 1) * nearest

    return SuccessResponse(
        data={
            "merchants": _round_up(store_count),
            "orders_processed": _round_up(order_count, 100),
            "waitlist_signups": _round_up(waitlist_count),
        },
        message="Platform statistics",
    )


@router.get(
    "/features", summary="Feature list for marketing page", operation_id="get_features"
)
async def get_features():
    """Return the NUMU feature list for the landing page.

    This is a static list managed in code rather than a CMS,
    keeping the marketing page fast and cache-friendly.
    """
    features = [
        {
            "key": "storefront",
            "title": "Bilingual Storefront",
            "title_ar": "واجهة متجر ثنائية اللغة",
            "description": "Arabic-first storefront with full English support. RTL layout, custom themes, and your own subdomain.",
            "icon": "store",
        },
        {
            "key": "payments",
            "title": "Egyptian Payment Gateways",
            "title_ar": "بوابات الدفع المصرية",
            "description": "Paymob cards & wallets, Fawry retail payments, and cash-on-delivery out of the box.",
            "icon": "credit_card",
        },
        {
            "key": "eta",
            "title": "ETA E-Invoicing",
            "title_ar": "الفاتورة الإلكترونية",
            "description": "Automatic Egyptian Tax Authority e-invoice generation, submission, and QR code compliance.",
            "icon": "receipt",
        },
        {
            "key": "shipping",
            "title": "Bosta Shipping Integration",
            "title_ar": "تكامل بوسطة للشحن",
            "description": "Create shipments, print AWBs, and track deliveries across Egypt with one click.",
            "icon": "local_shipping",
        },
        {
            "key": "whatsapp",
            "title": "WhatsApp Notifications",
            "title_ar": "إشعارات واتساب",
            "description": "Order confirmations, shipping updates, and marketing messages via WhatsApp Business API.",
            "icon": "chat",
        },
        {
            "key": "analytics",
            "title": "Real-time Analytics",
            "title_ar": "تحليلات فورية",
            "description": "Sales dashboards, conversion funnels, and inventory alerts — all in real time.",
            "icon": "analytics",
        },
        {
            "key": "multitenancy",
            "title": "Enterprise Multi-Tenancy",
            "title_ar": "عزل البيانات المؤسسي",
            "description": "Each store gets isolated data with PostgreSQL Row-Level Security. Your data stays yours.",
            "icon": "security",
        },
        {
            "key": "api",
            "title": "Developer-Friendly API",
            "title_ar": "واجهة برمجية للمطورين",
            "description": "Full REST API with OpenAPI docs, webhooks, and headless commerce support.",
            "icon": "code",
        },
    ]

    return SuccessResponse(
        data={"features": features, "count": len(features)},
        message="Feature list",
    )


@router.get(
    "/landing-config",
    summary="Landing page section configuration",
    operation_id="get_public_landing_config",
)
async def get_public_landing_config(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return landing page section visibility for the frontend.

    Returns the default configuration if no custom config exists yet.
    This is a public endpoint — no authentication required.
    """
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == "landing_page")
    )
    config = result.scalar_one_or_none()

    data = config.value if config else DEFAULT_LANDING_CONFIG

    return SuccessResponse(
        data=data,
        message="Landing page configuration",
    )


@router.get(
    "/merchant-hub-nav",
    summary="Merchant hub nav config (public read)",
    operation_id="get_public_merchant_hub_nav",
)
async def get_public_merchant_hub_nav(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Tabs the merchant hub should show, hide, mark "coming soon", and order.

    Reads the same platform_config row the admin endpoint writes. Kept public
    so the merchant hub can fetch it on first paint without authenticating —
    there are no secrets in this payload, just UI toggles.
    """
    from src.api.v1.routes.admin import merchant_hub_nav as nav_module

    NAV_KEY = nav_module.CONFIG_KEY
    DEFAULT_NAV = nav_module.DEFAULT_CONFIG
    _merge_with_defaults = nav_module._merge_with_defaults

    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == NAV_KEY)
    )
    config = result.scalar_one_or_none()
    data = _merge_with_defaults(config.value) if config else DEFAULT_NAV
    return SuccessResponse(data=data, message="Merchant hub nav config")


# Default pricing plans served to the landing page. Admin can override
# via PUT /admin/platform-config/pricing_plans.
DEFAULT_PRICING_PLANS = {
    "plans": [
        {
            "key": "trial",
            "name_en": "30-Day Free Trial",
            "name_ar": "تجربة مجانية ٣٠ يوم",
            "price_monthly": 0,
            "price_annual": 0,
            "currency": "EGP",
            "cta": "try_demo",
            "popular": False,
            "features": [
                {"en": "100 products", "ar": "١٠٠ منتج"},
                {"en": "Custom domain", "ar": "دومين خاص"},
                {"en": "All 12 premium themes", "ar": "كل الـ ١٢ ثيم"},
                {"en": "Basic analytics", "ar": "تحليلات أساسية"},
                {"en": "WhatsApp support", "ar": "دعم واتساب"},
            ],
        },
        {
            "key": "starter",
            "name_en": "Starter",
            "name_ar": "ستارتر",
            "price_monthly": 99,
            "price_annual": 990,
            "currency": "EGP",
            "cta": "subscribe",
            "popular": False,
            "features": [
                {"en": "100 products", "ar": "١٠٠ منتج"},
                {"en": "Custom domain", "ar": "دومين خاص"},
                {"en": "All 12 premium themes", "ar": "كل الـ ١٢ ثيم"},
                {"en": "Discount codes", "ar": "أكواد خصم"},
                {"en": "3 staff members", "ar": "٣ أعضاء فريق"},
                {"en": "Webhooks", "ar": "ويب هوكس"},
            ],
        },
        {
            "key": "pro",
            "name_en": "Pro",
            "name_ar": "برو",
            "price_monthly": 299,
            "price_annual": 2990,
            "currency": "EGP",
            "cta": "subscribe",
            "popular": True,
            "features": [
                {"en": "Unlimited products", "ar": "منتجات بلا حدود"},
                {"en": "Advanced analytics", "ar": "تحليلات متقدمة"},
                {"en": "Automations", "ar": "أتمتة"},
                {"en": "Abandoned cart recovery", "ar": "استرداد السلات المتروكة"},
                {"en": "API access", "ar": "وصول API"},
                {"en": "10 staff members", "ar": "١٠ أعضاء فريق"},
                {"en": "Priority support", "ar": "دعم أولوية"},
            ],
        },
        {
            "key": "enterprise",
            "name_en": "Enterprise",
            "name_ar": "إنتربرايز",
            "price_monthly": -1,
            "price_annual": -1,
            "currency": "EGP",
            "cta": "contact",
            "popular": False,
            "features": [
                {"en": "Everything in Pro", "ar": "كل مميزات برو"},
                {"en": "Multi-store", "ar": "متاجر متعددة"},
                {"en": "Dedicated account manager", "ar": "مدير حساب مخصص"},
                {"en": "SLA & uptime guarantee", "ar": "ضمان SLA"},
                {"en": "White-glove onboarding", "ar": "إعداد مخصص"},
            ],
        },
    ],
    "promo": {
        "code": "LAUNCH50",
        "text_en": "Launch offer: 50% off first 3 months with code LAUNCH50",
        "text_ar": "عرض الإطلاق: ٥٠٪ خصم أول ٣ شهور بكود LAUNCH50",
    },
}


@router.get(
    "/pricing-plans",
    summary="Pricing plans for landing page (admin-editable)",
    operation_id="get_public_pricing_plans",
)
async def get_public_pricing_plans(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return pricing plan data for the landing page.

    If an admin has customized the plans via the backoffice, those
    overrides are returned. Otherwise, falls back to the hardcoded
    defaults. This lets the admin change prices, feature lists, and
    promo banners without a code deploy.
    """
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == "pricing_plans")
    )
    config = result.scalar_one_or_none()

    data = config.value if config else DEFAULT_PRICING_PLANS

    return SuccessResponse(data=data, message="Pricing plans")
