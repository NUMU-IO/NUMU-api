"""GetPromotionAnalyticsUseCase — read aggregate counts for a promotion.

In v1 this reads from the live `promotion_events` table via the repo's
aggregate methods. The daily-rollup table from step 13 is a future
optimization that will plug in behind the same interface.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from src.application.dto.promotion_event import PromotionAnalyticsOutput
from src.core.exceptions.promotion_exceptions import PromotionNotFound
from src.core.interfaces.repositories.promotion_event_repository import (
    IPromotionEventRepository,
)
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionRepository,
)


class GetPromotionAnalyticsUseCase:
    """Aggregate counts for a single promotion within a date range."""

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        event_repo: IPromotionEventRepository,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._event_repo = event_repo

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        range_start: date | None = None,
        range_end: date | None = None,
    ) -> PromotionAnalyticsOutput:
        promo = await self._promotion_repo.get_by_id(store_id, promotion_id)
        if promo is None or promo.tenant_id != tenant_id:
            raise PromotionNotFound(str(promotion_id))

        rng_end = range_end or date.today()
        rng_start = range_start or (rng_end - timedelta(days=29))
        since = datetime.combine(rng_start, datetime.min.time(), tzinfo=UTC)
        until = datetime.combine(rng_end, datetime.max.time(), tzinfo=UTC)

        counts = await self._event_repo.counts_for_promotion(
            promotion_id, since=since, until=until
        )

        impressions = counts.impressions
        clicks = counts.clicks
        redemptions = counts.redemptions
        conversions = counts.conversions

        impression_to_click = clicks / impressions if impressions else 0.0
        conversion_rate = conversions / impressions if impressions else 0.0

        return PromotionAnalyticsOutput(
            promotion_id=promotion_id,
            range_start=rng_start,
            range_end=rng_end,
            impressions=impressions,
            clicks=clicks,
            dismissals=counts.dismissals,
            redemptions=redemptions,
            conversions=conversions,
            revenue_cents=counts.revenue_cents,
            discount_total_cents=counts.revenue_cents,  # placeholder until step 13 splits
            by_day=[],  # filled in by step 13's daily rollup
            conversion_rate=round(conversion_rate, 4),
            impression_to_click_rate=round(impression_to_click, 4),
            generated_at=datetime.now(UTC),
        )
