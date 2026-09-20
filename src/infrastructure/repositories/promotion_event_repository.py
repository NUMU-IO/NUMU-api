"""SQLAlchemy implementation of `IPromotionEventRepository`."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.promotion_event import PromotionEvent
from src.core.enums.promotion_enums import PromotionEventType
from src.core.interfaces.repositories.promotion_event_repository import (
    IPromotionEventRepository,
    PromotionEventCounts,
)
from src.infrastructure.database.models.tenant.promotion import PromotionEventModel
from src.infrastructure.mappers.promotion_mapper import PromotionMapper


class PromotionEventRepository(IPromotionEventRepository):
    def __init__(
        self,
        session: AsyncSession,
        mapper: PromotionMapper | None = None,
    ) -> None:
        self.session = session
        self.mapper = mapper or PromotionMapper()

    # ------------------------------------------------------------------ #
    # Writes                                                              #
    # ------------------------------------------------------------------ #

    async def record(self, event: PromotionEvent) -> None:
        self.session.add(self.mapper.event_to_orm(event))
        await self.session.flush()

    async def record_many(self, events: list[PromotionEvent]) -> None:
        self.session.add_all([self.mapper.event_to_orm(e) for e in events])
        await self.session.flush()

    # ------------------------------------------------------------------ #
    # Aggregations                                                        #
    # ------------------------------------------------------------------ #

    async def counts_for_promotion(
        self,
        promotion_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> PromotionEventCounts:
        filters = [PromotionEventModel.promotion_id == promotion_id]
        if since is not None:
            filters.append(PromotionEventModel.occurred_at >= since)
        if until is not None:
            filters.append(PromotionEventModel.occurred_at <= until)

        # `revenue` is the order value a convert event carries, written by
        # the convert handler into `metadata.order_total` (major units).
        # `discount` is what the offer took off, on whichever event type
        # recorded it. Summing them in the same GROUP BY keeps this one
        # query — the merchant detail page fires it on every page view.
        order_total = cast(
            PromotionEventModel.event_metadata["order_total"].astext, Numeric
        )
        stmt = (
            select(
                PromotionEventModel.event_type,
                func.count().label("cnt"),
                func.coalesce(
                    func.sum(PromotionEventModel.discount_amount_cents), 0
                ).label("discount"),
                func.coalesce(func.sum(order_total), 0).label("revenue_major"),
            )
            .where(*filters)
            .group_by(PromotionEventModel.event_type)
        )
        rows = (await self.session.execute(stmt)).all()

        impressions = clicks = dismissals = redemptions = conversions = 0
        revenue = discount = 0
        for event_type, cnt, disc, revenue_major in rows:
            cnt_int = int(cnt or 0)
            if event_type == "impression":
                impressions = cnt_int
            elif event_type == "click":
                clicks = cnt_int
            elif event_type == "dismiss":
                dismissals = cnt_int
            elif event_type == "redeem":
                redemptions = cnt_int
            elif event_type == "convert":
                conversions = cnt_int
                revenue = int(Decimal(revenue_major or 0) * 100)
            # Discount is reported on redeem AND convert rows; sum both so a
            # promotion that only ever recorded converts still reports what
            # it gave away (it used to read 0 on every code offer).
            if event_type in ("redeem", "convert"):
                discount += int(disc or 0)
        return PromotionEventCounts(
            promotion_id=promotion_id,
            impressions=impressions,
            clicks=clicks,
            dismissals=dismissals,
            redemptions=redemptions,
            conversions=conversions,
            revenue_cents=revenue,
            discount_total_cents=discount,
        )

    async def count_conversions_for_customer(
        self, promotion_id: UUID, customer_id: UUID
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(PromotionEventModel)
            .where(
                PromotionEventModel.promotion_id == promotion_id,
                PromotionEventModel.customer_id == customer_id,
                PromotionEventModel.event_type == "convert",
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def counts_for_store(
        self,
        store_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        event_types: list[PromotionEventType] | None = None,
    ) -> dict[UUID, PromotionEventCounts]:
        filters = [PromotionEventModel.store_id == store_id]
        if since is not None:
            filters.append(PromotionEventModel.occurred_at >= since)
        if until is not None:
            filters.append(PromotionEventModel.occurred_at <= until)
        if event_types:
            filters.append(
                PromotionEventModel.event_type.in_([e.value for e in event_types])
            )

        # Aggregate in a single round-trip: one row per promotion, with
        # per-event-type counts via FILTER (CASE WHEN ...).
        c = case
        stmt = (
            select(
                PromotionEventModel.promotion_id,
                func.count(
                    c((PromotionEventModel.event_type == "impression", 1))
                ).label("impressions"),
                func.count(c((PromotionEventModel.event_type == "click", 1))).label(
                    "clicks"
                ),
                func.count(c((PromotionEventModel.event_type == "dismiss", 1))).label(
                    "dismissals"
                ),
                func.count(c((PromotionEventModel.event_type == "redeem", 1))).label(
                    "redemptions"
                ),
                func.count(c((PromotionEventModel.event_type == "convert", 1))).label(
                    "conversions"
                ),
                func.coalesce(
                    func.sum(
                        c((
                            PromotionEventModel.event_type == "redeem",
                            PromotionEventModel.discount_amount_cents,
                        ))
                    ),
                    0,
                ).label("revenue"),
            )
            .where(*filters)
            .group_by(PromotionEventModel.promotion_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {
            row.promotion_id: PromotionEventCounts(
                promotion_id=row.promotion_id,
                impressions=int(row.impressions or 0),
                clicks=int(row.clicks or 0),
                dismissals=int(row.dismissals or 0),
                redemptions=int(row.redemptions or 0),
                conversions=int(row.conversions or 0),
                revenue_cents=int(row.revenue or 0),
            )
            for row in rows
        }
