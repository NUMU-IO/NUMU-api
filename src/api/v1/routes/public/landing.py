"""Public landing page endpoints — no auth required.

URL: /api/v1/public/stats, /api/v1/public/features
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stats", summary="Public platform statistics for landing page")
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


@router.get("/features", summary="Feature list for marketing page")
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
