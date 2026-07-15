"""Chart-annotation repository — store events to overlay on time-series.

Merchants read a sales dip or spike far better when the chart shows WHAT
happened: a coupon launch, a campaign send, a new product, a theme
publish. This repo gathers those events (already recorded across the
product/coupon/campaign/theme tables) into a uniform
``{date, type, label}`` stream the charts can pin markers on.

Store-local dates are the caller's job — this returns UTC instants; the
route projects them onto the store's wall clock so markers land on the
same day buckets the charts use.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.coupon import CouponModel
from src.infrastructure.database.models.tenant.marketing_campaign import (
    MarketingCampaignModel,
)
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.theme_customization_version import (
    ThemeCustomizationVersionModel,
)

# Per-source cap so a bulk import (hundreds of products in one day) can't
# bury the chart in markers — the caller further de-clutters by day.
_PER_SOURCE_LIMIT = 50


class AnnotationRepository:
    """Gathers store events for chart annotations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_events(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """Return ``[{at: datetime, type: str, label: str}]`` in the window.

        Types: ``product`` | ``coupon`` | ``campaign`` | ``theme``.
        Each source is tenant-scoped where the model carries tenant_id
        (theme versions are store-scoped only).
        """
        tid = get_tenant_id()
        events: list[dict] = []

        # ── Products launched (created) in the window ──
        prod_q = (
            select(ProductModel.name, ProductModel.created_at)
            .where(
                ProductModel.store_id == store_id,
                ProductModel.created_at >= date_from,
                ProductModel.created_at <= date_to,
            )
            .order_by(ProductModel.created_at.desc())
            .limit(_PER_SOURCE_LIMIT)
        )
        if tid:
            prod_q = prod_q.where(ProductModel.tenant_id == tid)
        for row in (await self.session.execute(prod_q)).all():
            events.append({
                "at": row.created_at,
                "type": "product",
                "label": row.name or "Product",
            })

        # ── Coupons that started (valid_from, else created) ──
        coupon_start = func.coalesce(CouponModel.valid_from, CouponModel.created_at)
        coupon_q = (
            select(CouponModel.code, coupon_start.label("at"))
            .where(
                CouponModel.store_id == store_id,
                coupon_start >= date_from,
                coupon_start <= date_to,
            )
            .order_by(coupon_start.desc())
            .limit(_PER_SOURCE_LIMIT)
        )
        if tid:
            coupon_q = coupon_q.where(CouponModel.tenant_id == tid)
        for row in (await self.session.execute(coupon_q)).all():
            events.append({
                "at": row.at,
                "type": "coupon",
                "label": f"Coupon {row.code}",
            })

        # ── Campaigns launched (started_at) ──
        camp_q = (
            select(MarketingCampaignModel.name, MarketingCampaignModel.started_at)
            .where(
                MarketingCampaignModel.store_id == store_id,
                MarketingCampaignModel.started_at.isnot(None),
                MarketingCampaignModel.started_at >= date_from,
                MarketingCampaignModel.started_at <= date_to,
            )
            .order_by(MarketingCampaignModel.started_at.desc())
            .limit(_PER_SOURCE_LIMIT)
        )
        if tid:
            camp_q = camp_q.where(MarketingCampaignModel.tenant_id == tid)
        for row in (await self.session.execute(camp_q)).all():
            events.append({
                "at": row.started_at,
                "type": "campaign",
                "label": row.name or "Campaign",
            })

        # ── Theme versions published (store-scoped only — no tenant_id) ──
        theme_q = (
            select(
                ThemeCustomizationVersionModel.version_label,
                ThemeCustomizationVersionModel.created_at,
            )
            .where(
                ThemeCustomizationVersionModel.store_id == store_id,
                ThemeCustomizationVersionModel.is_published.is_(True),
                ThemeCustomizationVersionModel.created_at >= date_from,
                ThemeCustomizationVersionModel.created_at <= date_to,
            )
            .order_by(ThemeCustomizationVersionModel.created_at.desc())
            .limit(_PER_SOURCE_LIMIT)
        )
        for row in (await self.session.execute(theme_q)).all():
            events.append({
                "at": row.created_at,
                "type": "theme",
                "label": row.version_label or "Theme published",
            })

        events.sort(key=lambda e: e["at"])
        return events
