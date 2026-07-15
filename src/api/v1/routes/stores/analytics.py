"""Analytics routes nested under stores.

URL: /stores/{store_id}/analytics
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_category_repository,
    get_customer_repository,
    get_db,
    get_order_repository,
    get_product_repository,
    verify_store_ownership,
)
from src.api.dependencies.date_range import DateRangeWindow, get_date_range_window
from src.api.dependencies.repositories import (
    get_analytics_repository,
    get_analytics_rollup_repository,
    get_annotation_repository,
    get_funnel_event_repository,
    get_metric_target_repository,
    get_page_view_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.application.services.health_score_service import (
    HEALTH_SCORE_WINDOW_DAYS,
    build_empty_state_message,
    build_recommendations,
    calculate_store_health_score,
)
from src.core.entities.order import OrderStatus, PaymentStatus
from src.core.entities.store import Store
from src.core.utils.store_timezone import (
    local_day_bounds,
    resolve_store_timezone_name,
    safe_zone,
)
from src.infrastructure.repositories import (
    AnalyticsRollupRepository,
    CategoryRepository,
    CustomerRepository,
    OrderRepository,
    StoreRepository,
)
from src.infrastructure.repositories.analytics_repository import AnalyticsRepository
from src.infrastructure.repositories.annotation_repository import (
    AnnotationRepository,
)
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)
from src.infrastructure.repositories.metric_target_repository import (
    MetricTargetRepository,
)
from src.infrastructure.repositories.page_view_repository import PageViewRepository
from src.infrastructure.repositories.product_repository import ProductRepository

router = APIRouter(prefix="/{store_id}/analytics")


class SalesOverviewResponse(BaseModel):
    """Sales overview statistics.

    ``total_sales`` is BOOKED revenue — every order except cancelled/
    refunded, including still-unpaid COD (the dominant payment method in
    Egypt: an order booked today is typically collected on delivery days
    later). ``collected_revenue`` is the money actually received: paid
    orders minus completed refunds — the Shopify-style gross/net split so
    merchants see both realities side by side.
    """

    total_sales: int  # In cents — booked (see docstring)
    total_orders: int
    avg_order_value: int  # In cents
    sales_change_percent: float
    orders_change_percent: float
    collected_revenue: int  # In cents — paid minus refunds
    collected_change_percent: float
    # Previous-window absolutes (same length, ending the day before the
    # current window) so the UI can show "vs EGP X" and derive deltas
    # for ratios like AOV without another round-trip.
    previous_total_sales: int
    previous_total_orders: int
    previous_collected_revenue: int
    currency: str


class SalesDataPointResponse(BaseModel):
    """Sales data point for charts.

    The ``prev_*`` fields are present only when the caller asked for a
    comparison series (``?compare=previous_period|previous_year``); each
    point carries the value of the SAME-POSITION bucket in the previous
    window, so charts can overlay the two series without re-aligning.
    """

    date: str
    sales: int
    orders: int
    prev_date: str | None = None
    prev_sales: int | None = None
    prev_orders: int | None = None


class AnalyticsTopProductResponse(BaseModel):
    """Top product by sales (analytics view with percentage)."""

    id: str
    name: str
    sku: str | None
    quantity_sold: int
    revenue: int  # In cents
    percentage: float


class SalesByLocationResponse(BaseModel):
    """Sales by location/governorate."""

    location: str
    sales: int  # In cents
    orders: int
    percentage: float


class CustomerAnalyticsResponse(BaseModel):
    """Customer analytics."""

    total_customers: int
    new_customers: int
    returning_customers: int
    avg_customer_value: int  # In cents


class ConversionStatsResponse(BaseModel):
    """Conversion statistics."""

    total_visitors: int  # Placeholder - would need analytics integration
    total_orders: int
    conversion_rate: float
    cart_abandonment_rate: float  # Placeholder


@router.get(
    "/overview",
    response_model=SuccessResponse[SalesOverviewResponse],
    summary="Get sales overview",
    operation_id="get_sales_overview",
)
async def get_sales_overview(
    store: Annotated[Store, Depends(verify_store_ownership)],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get sales overview for the store (uses pre-aggregated rollup data)."""
    today = window.end_date
    period_start = window.start_date

    # Comparison window: same number of calendar days, ending the day
    # BEFORE the current window starts. `get_aggregated` is inclusive on
    # both ends, so the previous end must be `period_start - 1 day` —
    # the old code passed `period_start` itself, counting that day in
    # BOTH windows and making the previous window one day longer than
    # the current one (the "vs previous period" % disagreed with the
    # dashboard's correctly-computed version of the same number).
    span = timedelta(days=window.days)
    previous_period_start = period_start - span
    previous_period_end = period_start - timedelta(days=1)

    # Try rollup table first
    current = await rollup_repo.get_aggregated(store.id, period_start, today)
    previous = await rollup_repo.get_aggregated(
        store.id, previous_period_start, previous_period_end
    )

    current_revenue = current["total_revenue_cents"]
    current_orders = current["total_orders"]
    previous_revenue = previous["total_revenue_cents"]
    previous_orders = previous["total_orders"]

    # If rollup is empty, fall back to raw query (first run / no rollup yet)
    if current_revenue == 0 and current_orders == 0:
        now = window.end
        ps = window.start
        # Instant math for the fallback: previous window has the exact
        # duration of the current one and ends just before it starts
        # (the repo range is inclusive on both ends).
        instant_span = now - ps
        prev_end = ps - timedelta(microseconds=1)
        pps = ps - instant_span
        current_revenue = await order_repo.get_revenue_by_date_range(store.id, ps, now)
        current_orders = await order_repo.count_by_store(
            store.id, date_from=ps, date_to=now
        )
        previous_revenue = await order_repo.get_revenue_by_date_range(
            store.id, pps, prev_end
        )
        previous_orders = await order_repo.count_by_store(
            store.id, date_from=pps, date_to=prev_end
        )

    # Collected revenue — money actually received: paid orders minus
    # completed refunds. Same sources /revenue-breakdown uses; refunds
    # ride on the rollup aggregates fetched above (0 when the rollup is
    # empty — a brand-new store has no refunds to subtract anyway).
    # Negative values are possible and honest (a refund landing in a
    # window with little new payment).
    instant_span = window.end - window.start
    prev_instant_start = window.start - instant_span
    prev_instant_end = window.start - timedelta(microseconds=1)
    paid_current = await analytics_repo.revenue_summary_paid(
        store.id, window.start, window.end
    )
    paid_previous = await analytics_repo.revenue_summary_paid(
        store.id, prev_instant_start, prev_instant_end
    )
    collected_current = paid_current["gross_cents"] - current["refund_amount_cents"]
    collected_previous = paid_previous["gross_cents"] - previous["refund_amount_cents"]

    # Calculate changes
    if previous_revenue > 0:
        sales_change = ((current_revenue - previous_revenue) / previous_revenue) * 100
    else:
        sales_change = 100.0 if current_revenue > 0 else 0.0

    if previous_orders > 0:
        orders_change = ((current_orders - previous_orders) / previous_orders) * 100
    else:
        orders_change = 100.0 if current_orders > 0 else 0.0

    if collected_previous > 0:
        collected_change = (
            (collected_current - collected_previous) / collected_previous
        ) * 100
    else:
        collected_change = 100.0 if collected_current > 0 else 0.0

    avg_order_value = current_revenue // current_orders if current_orders > 0 else 0

    return SuccessResponse(
        data=SalesOverviewResponse(
            total_sales=current_revenue,
            total_orders=current_orders,
            avg_order_value=avg_order_value,
            sales_change_percent=round(sales_change, 1),
            orders_change_percent=round(orders_change, 1),
            collected_revenue=collected_current,
            collected_change_percent=round(collected_change, 1),
            previous_total_sales=previous_revenue,
            previous_total_orders=previous_orders,
            previous_collected_revenue=collected_previous,
            currency=store.default_currency.value if store.default_currency else "EGP",
        ),
        message="Sales overview retrieved successfully",
    )


@router.get(
    "/sales-chart",
    response_model=SuccessResponse[list[SalesDataPointResponse]],
    summary="Get sales chart data",
    operation_id="get_sales_chart",
)
async def get_sales_chart(
    store: Annotated[Store, Depends(verify_store_ownership)],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
    compare: Annotated[
        Literal["previous_period", "previous_year"] | None,
        Query(
            description="Attach an aligned comparison series to each point: "
            "previous_period = same length ending the day before the window "
            "starts; previous_year = same dates one year earlier. Ignored "
            "for granularity=hour."
        ),
    ] = None,
):
    """Get sales data for chart visualization (single query via rollup).

    Bucket size follows ``window.granularity``: rollups are always daily
    in the source table, so we group on the fly for ``week``/``month``
    and synthesize hourly buckets from raw orders when ``hour`` is
    requested.
    """
    today = window.end_date
    date_from = window.start_date
    days = (today - date_from).days + 1

    # Hourly bucketing for short ranges (≤ 7 days enforced by the
    # dependency). Bypasses the rollup table since it's day-grained.
    if window.granularity == "hour":
        rows = await order_repo.get_daily_aggregates(
            store.id, window.start, window.end, timezone=window.tz
        )
        # `get_daily_aggregates` returns daily rows; for hourly precision
        # we just emit a day per bucket — finer aggregation is a
        # follow-up that needs a new repo method.
        data_points = [
            SalesDataPointResponse(
                date=row[0].strftime("%b %d %H:00")
                if hasattr(row[0], "hour")
                else row[0].strftime("%b %d"),
                sales=row[1],
                orders=row[2],
            )
            for row in rows
        ]
        return SuccessResponse(
            data=data_points,
            message="Sales chart data retrieved successfully",
        )

    def _bucket_key(d: date) -> date:
        if window.granularity == "week":
            return d - timedelta(days=d.weekday())  # Monday-start
        if window.granularity == "month":
            return d.replace(day=1)
        return d

    def _bucket_label(d: date) -> str:
        if window.granularity == "month":
            return d.strftime("%b %Y")
        if window.granularity == "week":
            return d.strftime("Wk of %b %d")
        return d.strftime("%b %d")

    async def _bucketed_series(from_d: date, to_d: date) -> list[tuple[date, int, int]]:
        """Zero-filled, bucketed ``(bucket_key, sales, orders)`` for a
        window. Rollup-first with the live-SQL fallback — each window
        decides independently (the previous window often has rollups
        even when the current day doesn't yet)."""
        n = (to_d - from_d).days + 1
        daily: list[tuple[date, int, int]]
        rollups = await rollup_repo.get_range(store.id, from_d, to_d)
        if rollups:
            rollup_map = {r.rollup_date: r for r in rollups}
            daily = []
            for i in range(n):
                d = from_d + timedelta(days=i)
                r = rollup_map.get(d)
                daily.append((
                    d,
                    r.total_revenue_cents if r else 0,
                    r.total_orders if r else 0,
                ))
        else:
            # Rollup table empty (first run of the day, brand-new
            # install). Single GROUP-BY-day query; `from_d`/`to_d` are
            # store-local calendar dates — expand to UTC instants.
            start_dt, _ = local_day_bounds(from_d, window.tz)
            _, end_dt = local_day_bounds(to_d, window.tz)
            rows = await order_repo.get_daily_aggregates(
                store.id, start_dt, end_dt, timezone=window.tz
            )
            by_day = {row[0]: row for row in rows}
            daily = []
            for i in range(n):
                d = from_d + timedelta(days=i)
                row = by_day.get(d)
                daily.append((d, row[1] if row else 0, row[2] if row else 0))

        agg: dict[date, tuple[int, int]] = {}
        order_lookup: list[date] = []
        for d, sales, orders in daily:
            bk = _bucket_key(d)
            if bk not in agg:
                agg[bk] = (0, 0)
                order_lookup.append(bk)
            s, o = agg[bk]
            agg[bk] = (s + sales, o + orders)
        return [(bk, agg[bk][0], agg[bk][1]) for bk in order_lookup]

    current = await _bucketed_series(date_from, today)

    previous: list[tuple[date, int, int]] | None = None
    if compare == "previous_year":
        previous = await _bucketed_series(
            date_from - timedelta(days=365), today - timedelta(days=365)
        )
    elif compare == "previous_period":
        previous = await _bucketed_series(
            date_from - timedelta(days=days), date_from - timedelta(days=1)
        )

    data_points = [
        SalesDataPointResponse(
            date=_bucket_label(bk),
            sales=sales,
            orders=orders,
            # Positional alignment — bucket i of the current window pairs
            # with bucket i of the previous one (the standard "compare"
            # convention; windows are the same length by construction).
            prev_date=_bucket_label(previous[i][0])
            if previous and i < len(previous)
            else None,
            prev_sales=previous[i][1] if previous and i < len(previous) else None,
            prev_orders=previous[i][2] if previous and i < len(previous) else None,
        )
        for i, (bk, sales, orders) in enumerate(current)
    ]
    return SuccessResponse(
        data=data_points,
        message="Sales chart data retrieved successfully",
    )


@router.get(
    "/top-products",
    response_model=SuccessResponse[list[AnalyticsTopProductResponse]],
    summary="Get top selling products (analytics)",
    operation_id="get_analytics_top_products",
)
async def get_analytics_top_products(
    store: Annotated[Store, Depends(verify_store_ownership)],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
    limit: int = Query(5, ge=1, le=20),
):
    """Get top selling products.

    Preferred path: merge ``top_products_json`` across daily rollup rows
    (cheap — at most ``days`` rows × ~50 products each). Fallback when
    no products are present in the rollup window — either because the
    nightly task hasn't run yet OR because every rollup row's
    ``top_products_json`` is empty (legacy data, before the COD-pending
    fix) — falls through to a live SQL aggregation on the orders table.
    """
    today = window.end_date
    date_from = window.start_date

    rollups = await rollup_repo.get_range(store.id, date_from, today)

    merged: dict[str, dict] = {}
    for r in rollups or []:
        for item in r.top_products_json or []:
            pid = str(item.get("product_id", ""))
            if not pid:
                continue
            if pid not in merged:
                merged[pid] = {
                    "id": pid,
                    "name": item.get("name", ""),
                    "sku": item.get("sku"),
                    "quantity": 0,
                    "revenue": 0,
                }
            merged[pid]["quantity"] += item.get("quantity", 0)
            merged[pid]["revenue"] += item.get("revenue", 0)

    if merged:
        sorted_products = sorted(
            merged.values(), key=lambda x: x["revenue"], reverse=True
        )[:limit]
        total_revenue = sum(p["revenue"] for p in merged.values())
    else:
        # Fallback: rollup either hasn't run yet OR ran with the old
        # is_paid gate that produced empty top_products_json. The live
        # SQL path covers both cases — runs on demand, no rollup
        # dependency. Slightly more expensive but only triggers when
        # the rollup didn't have anything to merge.
        rows = await analytics_repo.top_products(
            store.id, window.start, window.end, limit=limit
        )
        # Older orders' line_items JSONB may not carry a ``name`` field;
        # ``jsonb_array_elements(...)['name'].astext`` returns NULL in
        # that case and Pydantic rejects ``name: str`` at the response
        # stage. Coalesce to a stable placeholder so the chart still
        # renders — matches the rollup branch above which uses ``""``.
        sorted_products = [
            {
                "id": r["product_id"],
                "name": r["product_name"] or "(unnamed product)",
                "sku": None,
                "quantity": r["units_sold"],
                "revenue": r["revenue_cents"],
            }
            for r in rows
        ]
        total_revenue = sum(p["revenue"] for p in sorted_products)

    return SuccessResponse(
        data=[
            AnalyticsTopProductResponse(
                id=str(p["id"]),
                name=p["name"],
                sku=p["sku"],
                quantity_sold=p["quantity"],
                revenue=p["revenue"],
                percentage=round(p["revenue"] / total_revenue * 100, 1)
                if total_revenue > 0
                else 0,
            )
            for p in sorted_products
        ],
        message="Top products retrieved successfully",
    )


@router.get(
    "/sales-by-location",
    response_model=SuccessResponse[list[SalesByLocationResponse]],
    summary="Get sales by location",
    operation_id="get_sales_by_location",
)
async def get_sales_by_location(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get sales breakdown by location/governorate.

    SQL ``GROUP BY LOWER(TRIM(shipping_address->>'city'))`` so the
    dashboard no longer shows ``Cairo`` and ``cairo`` as two separate
    rows. Falls back to ``state`` when ``city`` is missing.
    """
    rows = await analytics_repo.sales_by_location(store.id, window.start, window.end)
    total_sales = sum(r["revenue_cents"] for r in rows)

    return SuccessResponse(
        data=[
            SalesByLocationResponse(
                location=r["location"],
                sales=r["revenue_cents"],
                orders=r["orders"],
                percentage=(
                    round(r["revenue_cents"] / total_sales * 100, 1)
                    if total_sales > 0
                    else 0.0
                ),
            )
            for r in rows
        ],
        message="Sales by location retrieved successfully",
    )


@router.get(
    "/customers",
    response_model=SuccessResponse[CustomerAnalyticsResponse],
    summary="Get customer analytics",
    operation_id="get_customer_analytics",
)
async def get_customer_analytics(
    store: Annotated[Store, Depends(verify_store_ownership)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get customer analytics for the store.

    "New vs returning" comes from a CTE that joins active-in-window
    customers against their first-order timestamp across all time, so a
    customer whose first order pre-dates the window is "returning" even
    if their only window order was today.
    """
    period_start = window.start
    now = window.end

    total_customers = await customer_repo.count_by_store(store.id)
    split = await analytics_repo.new_vs_returning(store.id, period_start, now)
    period = await analytics_repo.period_revenue_and_unique_customers(
        store.id, period_start, now
    )
    avg_customer_value = (
        period["revenue_cents"] // period["unique_customers"]
        if period["unique_customers"] > 0
        else 0
    )

    return SuccessResponse(
        data=CustomerAnalyticsResponse(
            total_customers=total_customers,
            new_customers=split["new"],
            returning_customers=split["returning"],
            avg_customer_value=avg_customer_value,
        ),
        message="Customer analytics retrieved successfully",
    )


@router.get(
    "/conversion",
    response_model=SuccessResponse[ConversionStatsResponse],
    summary="Get conversion statistics",
    operation_id="get_conversion_stats",
)
async def get_conversion_stats(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get conversion statistics for the store."""
    period_start = window.start
    now = window.end

    # Real order count (SQL COUNT, no truncation), excluding cancelled/refunded
    # so the conversion rate isn't deflated by orders that never represented
    # paid revenue.
    total_orders = await order_repo.count_by_store(
        store.id,
        date_from=period_start,
        date_to=now,
        exclude_statuses=[OrderStatus.CANCELLED, OrderStatus.REFUNDED],
    )

    total_visitors = await pv_repo.count_unique_visitors(store.id, period_start, now)
    conversion_rate = (total_orders / total_visitors * 100) if total_visitors > 0 else 0

    # Cart abandonment: sessions that fired add_to_cart minus sessions that
    # completed an order, divided by add_to_cart sessions. Funnel event
    # counts are unique session_fingerprint counts so the math is in the
    # same unit on both sides.
    funnel_counts = await funnel_repo.get_funnel_counts(store.id, period_start, now)
    add_to_cart_sessions = funnel_counts.get("add_to_cart", 0)
    completed_sessions = funnel_counts.get("order_completed", 0)
    if add_to_cart_sessions > 0:
        abandoned = max(0, add_to_cart_sessions - completed_sessions)
        cart_abandonment_rate = round(abandoned / add_to_cart_sessions * 100, 2)
    else:
        cart_abandonment_rate = 0.0

    return SuccessResponse(
        data=ConversionStatsResponse(
            total_visitors=total_visitors,
            total_orders=total_orders,
            conversion_rate=round(conversion_rate, 2),
            cart_abandonment_rate=cart_abandonment_rate,
        ),
        message="Conversion stats retrieved successfully",
    )


class TrafficSourceResponse(BaseModel):
    """Traffic source attribution from UTM parameters."""

    source: str
    orders: int
    revenue: int  # cents
    percentage: float


@router.get(
    "/traffic-sources",
    response_model=SuccessResponse[list[TrafficSourceResponse]],
    summary="Get orders by traffic source",
    operation_id="get_traffic_sources",
)
async def get_traffic_sources(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get order attribution by UTM source.

    SQL ``GROUP BY LOWER(TRIM(utm_source))`` so casing collisions are
    collapsed; missing utm falls into a ``direct`` bucket. No row cap —
    the database does the aggregation, so big stores don't silently
    truncate at the old ``limit=5000`` boundary.
    """
    rows = await analytics_repo.traffic_sources(store.id, window.start, window.end)
    total_revenue = sum(r["revenue_cents"] for r in rows)

    return SuccessResponse(
        data=[
            TrafficSourceResponse(
                source=r["source"],
                orders=r["orders"],
                revenue=r["revenue_cents"],
                percentage=(
                    round(r["revenue_cents"] / total_revenue * 100, 1)
                    if total_revenue > 0
                    else 0.0
                ),
            )
            for r in rows
        ],
        message="Traffic sources retrieved successfully",
    )


class CodRejectionLocationResponse(BaseModel):
    """COD rejection stats for a single location."""

    location: str
    rejected: int
    total: int
    rate: float


class CodRejectionStatsResponse(BaseModel):
    """COD rejection statistics."""

    total_cod_shipments: int
    delivered_count: int
    rejected_count: int
    returned_count: int
    rejection_rate: float  # percentage
    total_cod_amount: int  # cents
    rejected_amount: int  # cents
    by_location: list[CodRejectionLocationResponse]


@router.get(
    "/cod-rejections",
    response_model=SuccessResponse[CodRejectionStatsResponse],
    summary="Get COD rejection statistics",
    operation_id="get_cod_rejection_stats",
)
async def get_cod_rejection_stats(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get COD rejection rate and breakdown by location.

    Sourced from the ``orders`` table directly. Earlier this endpoint
    queried ``shipments``, which is only populated for stores that
    integrate a courier (Bosta / MyLerz / J&T) — manual-fulfilment
    stores read zeros across the board. ``orders`` is the canonical
    source: courier webhooks already flip ``orders.status`` on
    delivered/returned, and manual merchants flip it via the merchant
    UI.
    """
    period_start = window.start
    now = window.end

    stats = await analytics_repo.cod_summary(store.id, period_start, now)
    locations = await analytics_repo.cod_rejections_by_location(
        store.id, period_start, now
    )

    rejection_rate = (
        round(stats["rejected"] / stats["total"] * 100, 1)
        if stats["total"] > 0
        else 0.0
    )

    return SuccessResponse(
        data=CodRejectionStatsResponse(
            total_cod_shipments=stats["total"],
            delivered_count=stats["delivered"],
            rejected_count=stats["rejected"],
            returned_count=stats["returned"],
            rejection_rate=rejection_rate,
            total_cod_amount=stats["total_cod_amount"],
            rejected_amount=stats["rejected_amount"],
            by_location=[CodRejectionLocationResponse(**loc) for loc in locations],
        ),
        message="COD rejection stats retrieved successfully",
    )


# ── Health Score ──


class HealthScoreMetrics(BaseModel):
    delivery_success_rate: float
    cod_acceptance_rate: float
    order_completion_rate: float
    return_rate: float
    avg_response_hours: float


class HealthScoreResponse(BaseModel):
    score: int | None
    grade: str
    insufficient_data: bool = False
    insufficient_metrics: list[str] = []
    metrics: HealthScoreMetrics
    sub_scores: dict[str, int]
    recommendations: list[str]
    orders_analyzed: int
    shipments_analyzed: int
    window_days: int = HEALTH_SCORE_WINDOW_DAYS
    empty_state_message: str | None = None
    calculated_at: str | None


# How long a cached score is allowed to be served before we recompute live.
# Celery refreshes every 24h, so 26h gives a small grace window for late
# task runs without ever serving genuinely stale numbers.
HEALTH_SCORE_CACHE_TTL_HOURS = 26


def _cache_is_fresh(cached: dict) -> bool:
    """Return True if the cached score was computed within the TTL window."""
    raw = cached.get("calculated_at")
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts) < timedelta(hours=HEALTH_SCORE_CACHE_TTL_HOURS)


@router.get(
    "/health-score",
    response_model=SuccessResponse[HealthScoreResponse],
    summary="Get merchant health score",
    operation_id="get_health_score",
)
async def get_health_score(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    live: bool = Query(
        False, description="Calculate live instead of using cached score"
    ),
    lang: str = Query("ar", description="Language for recommendations: ar or en"),
):
    """Get the merchant health score (cached daily or live calculation).

    On first call (no cache), calculates live and persists in store.settings
    so subsequent calls are instant. Celery refreshes the cache daily.
    Recommendations are regenerated per-request so they always match the
    user's current language.
    """
    normalized_lang = lang if lang in ("ar", "en") else "ar"

    # Try cached score first (from daily Celery task) — but only if fresh.
    if not live and store.settings:
        cached = store.settings.get("health_score")
        if cached and _cache_is_fresh(cached):
            # Backfill flags for caches written before this field existed.
            cached.setdefault("insufficient_data", False)
            cached.setdefault("insufficient_metrics", [])
            cached.setdefault("window_days", HEALTH_SCORE_WINDOW_DAYS)
            # Regenerate recommendations + empty-state copy in the requested
            # language so the merchant sees Arabic/English consistently with
            # their UI (both are language-dependent, the rest is numeric).
            cached["recommendations"] = build_recommendations(
                sub_scores=cached.get("sub_scores", {}),
                final_score=cached.get("score") or 0,
                insufficient_metrics=set(cached.get("insufficient_metrics", [])),
                lang=normalized_lang,
            )
            cached["empty_state_message"] = (
                build_empty_state_message(
                    normalized_lang,
                    cached.get("window_days", HEALTH_SCORE_WINDOW_DAYS),
                )
                if cached.get("insufficient_data")
                else None
            )
            return SuccessResponse(
                data=HealthScoreResponse(**cached),
                message="Health score retrieved (cached)",
            )

    # Live calculation (first visit, stale cache, or explicit live=true)

    score_data = await calculate_store_health_score(
        session=order_repo.session,
        store_id=store.id,
        days=HEALTH_SCORE_WINDOW_DAYS,
        lang=normalized_lang,
    )

    # Cache the result in store.settings for next time. We don't cache
    # "insufficient_data" snapshots — a brand-new store could get its
    # first orders in minutes, and we don't want to serve a stale empty
    # state for 24h until Celery overwrites it.
    if not score_data.get("insufficient_data"):
        try:
            store_repo = StoreRepository(order_repo.session)
            current_settings = dict(store.settings) if store.settings else {}
            current_settings["health_score"] = score_data
            store.settings = current_settings
            await store_repo.update(store)
        except Exception:
            pass  # Non-critical — score still returned even if caching fails

    return SuccessResponse(
        data=HealthScoreResponse(**score_data),
        message="Health score calculated",
    )


# ── Orders Breakdown ──


class OrdersByStatusItem(BaseModel):
    status: str
    count: int
    percentage: float


class OrdersByPaymentMethodItem(BaseModel):
    method: str
    count: int
    revenue: int  # cents


class FulfillmentTimeStats(BaseModel):
    avg_hours: float
    p50_hours: float
    p95_hours: float


class OrdersByDayOfWeekItem(BaseModel):
    day: str
    orders: int
    revenue: int  # cents


class OrdersByHourItem(BaseModel):
    hour: int
    orders: int


class OrdersBreakdownResponse(BaseModel):
    by_status: list[OrdersByStatusItem]
    by_payment_method: list[OrdersByPaymentMethodItem]
    fulfillment_time: FulfillmentTimeStats
    by_day_of_week: list[OrdersByDayOfWeekItem]
    by_hour_of_day: list[OrdersByHourItem]


@router.get(
    "/orders-breakdown",
    response_model=SuccessResponse[OrdersBreakdownResponse],
    summary="Get orders breakdown analytics",
    operation_id="get_orders_breakdown",
)
async def get_orders_breakdown(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get orders breakdown by status, payment method, time distribution.

    Five SQL aggregations replace the previous loop-over-5000-orders
    pattern. Day-of-week and hour-of-day come from ``EXTRACT(dow ...)``
    / ``EXTRACT(hour ...)``; fulfillment percentiles use
    ``percentile_cont`` so we don't sort a Python list of timedeltas.
    """
    period_start = window.start
    now = window.end

    status_map = await analytics_repo.orders_by_status(store.id, period_start, now)
    total = sum(status_map.values())
    by_status = [
        OrdersByStatusItem(
            status=s,
            count=c,
            percentage=round(c / total * 100, 1) if total > 0 else 0,
        )
        for s, c in sorted(status_map.items(), key=lambda x: x[1], reverse=True)
    ]

    method_map = await analytics_repo.orders_by_payment_method(
        store.id, period_start, now
    )
    by_payment_method = [
        OrdersByPaymentMethodItem(
            method=m, count=d["count"], revenue=d["revenue_cents"]
        )
        for m, d in sorted(
            method_map.items(), key=lambda x: x[1]["revenue_cents"], reverse=True
        )
    ]

    f = await analytics_repo.fulfillment_time_stats(store.id, period_start, now)
    fulfillment_time = FulfillmentTimeStats(
        avg_hours=f["avg_hours"],
        p50_hours=f["p50_hours"],
        p95_hours=f["p95_hours"],
    )

    # Postgres EXTRACT(dow): 0=Sunday … 6=Saturday. Display order: Mon–Sun.
    day_names = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    pg_to_iso = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 0: 6}
    dow_raw = await analytics_repo.orders_by_day_of_week(
        store.id, period_start, now, tz=window.tz
    )
    by_iso: dict[int, dict] = {i: {"orders": 0, "revenue_cents": 0} for i in range(7)}
    for pg_dow, vals in dow_raw.items():
        iso = pg_to_iso.get(pg_dow, 0)
        by_iso[iso] = vals
    by_day_of_week = [
        OrdersByDayOfWeekItem(
            day=day_names[i],
            orders=by_iso[i]["orders"],
            revenue=by_iso[i]["revenue_cents"],
        )
        for i in range(7)
    ]

    hour_map = await analytics_repo.orders_by_hour(
        store.id, period_start, now, tz=window.tz
    )
    by_hour_of_day = [
        OrdersByHourItem(hour=h, orders=hour_map.get(h, 0)) for h in range(24)
    ]

    return SuccessResponse(
        data=OrdersBreakdownResponse(
            by_status=by_status,
            by_payment_method=by_payment_method,
            fulfillment_time=fulfillment_time,
            by_day_of_week=by_day_of_week,
            by_hour_of_day=by_hour_of_day,
        ),
        message="Orders breakdown retrieved successfully",
    )


# ── Revenue Breakdown ──


class CouponUsageItem(BaseModel):
    code: str
    uses: int
    revenue_impact: int  # cents (discount amount)


class TaxByRateItem(BaseModel):
    rate_pct: float
    orders: int
    tax_cents: int


class RevenueBreakdownResponse(BaseModel):
    gross_revenue: int  # cents
    discounts: int  # cents
    shipping_collected: int  # cents
    refunds: int  # cents
    net_revenue: int  # cents
    # Tax collected (paid orders). Egyptian VAT is INCLUSIVE — it is part
    # of gross, not added on top — so it is NOT subtracted in net_revenue;
    # it's what the merchant owes the tax authority out of that revenue.
    # ``tax_inclusive`` is the portion embedded in prices; ``tax_added``
    # the portion charged on top (exclusive tax).
    tax_collected: int  # cents
    tax_inclusive: int  # cents
    tax_added: int  # cents
    tax_by_rate: list[TaxByRateItem]
    coupon_usage: list[CouponUsageItem]


@router.get(
    "/revenue-breakdown",
    response_model=SuccessResponse[RevenueBreakdownResponse],
    summary="Get revenue breakdown analytics",
    operation_id="get_revenue_breakdown",
)
async def get_revenue_breakdown(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get revenue breakdown: gross, discounts, shipping, refunds, net.

    Two SQL aggregates (totals + coupon usage) replace the previous
    truncated 5000-order Python loop. Refunds still come from the daily
    rollup since refunds are tracked there with their own currency
    conversion logic.
    """
    period_start = window.start
    now = window.end
    today = window.end_date
    date_from_d = window.start_date

    summary = await analytics_repo.revenue_summary_paid(store.id, period_start, now)
    coupons = await analytics_repo.coupon_usage(store.id, period_start, now)
    tax = await analytics_repo.tax_by_rate(store.id, period_start, now)

    agg = await rollup_repo.get_aggregated(store.id, date_from_d, today)
    refunds = agg["refund_amount_cents"]
    net_revenue = summary["gross_cents"] - refunds

    return SuccessResponse(
        data=RevenueBreakdownResponse(
            gross_revenue=summary["gross_cents"],
            discounts=summary["discounts_cents"],
            shipping_collected=summary["shipping_cents"],
            refunds=refunds,
            net_revenue=net_revenue,
            tax_collected=summary["tax_cents"],
            tax_inclusive=tax["inclusive_cents"],
            tax_added=tax["added_cents"],
            tax_by_rate=[
                TaxByRateItem(
                    rate_pct=t["rate_pct"],
                    orders=t["orders"],
                    tax_cents=t["tax_cents"],
                )
                for t in tax["by_rate"]
            ],
            coupon_usage=[
                CouponUsageItem(
                    code=c["code"],
                    uses=c["uses"],
                    revenue_impact=c["revenue_impact"],
                )
                for c in coupons
            ],
        ),
        message="Revenue breakdown retrieved successfully",
    )


# ── Customer Segments (RFM) ──


class RFMSegmentItem(BaseModel):
    segment: str
    count: int
    percentage: float
    avg_revenue: int  # cents
    avg_orders: float


class CohortRow(BaseModel):
    cohort: str  # e.g. "2026-01"
    size: int
    retention: list[float]  # percentages for month+1, month+2, ...


class CLVStats(BaseModel):
    avg_clv: int  # cents
    median_clv: int  # cents
    top_10_pct_clv: int  # cents
    total_customers: int
    single_order_pct: float


class CustomerSegmentsResponse(BaseModel):
    segments: list[RFMSegmentItem]
    cohorts: list[CohortRow]
    clv: CLVStats


def _rfm_segment(r_score: int, f_score: int, m_score: int) -> str:
    """Map RFM scores (1-5 each) to a named segment."""
    if r_score >= 4 and f_score >= 4:
        return "Champions"
    if r_score >= 3 and f_score >= 3:
        return "Loyal"
    if r_score >= 4 and f_score <= 2:
        return "New"
    if r_score <= 2 and f_score >= 3:
        return "At Risk"
    if r_score <= 2 and f_score <= 2:
        return "Lost"
    return "Potential"


def _quintile(values: list[float], value: float) -> int:
    """Return 1-5 quintile score for a value within a sorted list."""
    if not values:
        return 3
    n = len(values)
    rank = sum(1 for v in values if v <= value)
    return min(int((rank / n) * 5) + 1, 5)


@router.get(
    "/customer-segments",
    response_model=SuccessResponse[CustomerSegmentsResponse],
    summary="Get customer RFM segmentation",
    operation_id="get_customer_segments",
)
async def get_customer_segments(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get customer segmentation using RFM analysis, cohort retention, and CLV.

    Three SQL aggregations replace the previous 10000-order in-memory
    scan: per-customer aggregates over the window, all-time first-order
    per active customer (drives cohort assignment), and the customer ×
    order-month matrix (drives retention). The Python side then loops
    over a customer-sized set, not an order-sized one.
    """
    period_start = window.start
    now = window.end

    aggregates = await analytics_repo.customer_period_aggregates(
        store.id, period_start, now
    )

    # Translate the SQL rows into the same per-customer dict shape the
    # downstream RFM/cohort scorer was built around. ``first_order`` is
    # rebound below to the all-time first order (cohort anchor).
    customer_stats: dict[UUID, dict] = {
        a["customer_id"]: {
            "orders": a["orders"],
            "total_spent": a["total_spent_cents"],
            "last_order": a["last_at"],
            "first_order": a["first_at"],
        }
        for a in aggregates
    }

    if not customer_stats:
        return SuccessResponse(
            data=CustomerSegmentsResponse(
                segments=[],
                cohorts=[],
                clv=CLVStats(
                    avg_clv=0,
                    median_clv=0,
                    top_10_pct_clv=0,
                    total_customers=0,
                    single_order_pct=0,
                ),
            ),
            message="Customer segments retrieved successfully",
        )

    # Calculate RFM values
    recencies = []
    frequencies = []
    monetaries = []
    for cs in customer_stats.values():
        r = (now - cs["last_order"]).days
        recencies.append(float(r))
        frequencies.append(float(cs["orders"]))
        monetaries.append(float(cs["total_spent"]))

    recencies_sorted = sorted(recencies)
    frequencies_sorted = sorted(frequencies)
    monetaries_sorted = sorted(monetaries)

    # Score each customer and assign segment
    segment_counts: dict[str, dict] = {}
    for _cid, cs in customer_stats.items():
        r_val = (now - cs["last_order"]).days
        # Recency: lower is better → invert score
        r_score = 6 - _quintile(recencies_sorted, float(r_val))
        f_score = _quintile(frequencies_sorted, float(cs["orders"]))
        m_score = _quintile(monetaries_sorted, float(cs["total_spent"]))

        seg = _rfm_segment(r_score, f_score, m_score)
        if seg not in segment_counts:
            segment_counts[seg] = {"count": 0, "total_revenue": 0, "total_orders": 0}
        segment_counts[seg]["count"] += 1
        segment_counts[seg]["total_revenue"] += cs["total_spent"]
        segment_counts[seg]["total_orders"] += cs["orders"]

    total_customers = len(customer_stats)
    segments = [
        RFMSegmentItem(
            segment=seg,
            count=d["count"],
            percentage=round(d["count"] / total_customers * 100, 1),
            avg_revenue=d["total_revenue"] // d["count"] if d["count"] > 0 else 0,
            avg_orders=round(d["total_orders"] / d["count"], 1)
            if d["count"] > 0
            else 0,
        )
        for seg, d in sorted(
            segment_counts.items(), key=lambda x: x[1]["count"], reverse=True
        )
    ]

    # Cohort retention (month-over-month).
    # Anchor the cohort on the customer's *all-time* first order, not
    # just the first one inside the analysis window — otherwise a long
    # tenured customer who only ordered today would wrongly be tagged
    # as a brand-new cohort.
    customer_ids = list(customer_stats.keys())
    all_time_first = await analytics_repo.customer_first_order_all_time(
        store.id, customer_ids
    )
    customer_order_months = await analytics_repo.customer_order_months(
        store.id, customer_ids
    )

    cohort_customers: dict[str, set[UUID]] = {}
    for cid in customer_ids:
        first_at = all_time_first.get(cid) or customer_stats[cid]["first_order"]
        cohort_key = first_at.strftime("%Y-%m")
        cohort_customers.setdefault(cohort_key, set()).add(cid)

    sorted_months = sorted(cohort_customers.keys())
    all_months = sorted({
        m for months in customer_order_months.values() for m in months
    })

    cohorts = []
    for cohort_month in sorted_months:
        cids = cohort_customers[cohort_month]
        size = len(cids)
        if size == 0:
            continue
        # Find subsequent months
        try:
            start_idx = all_months.index(cohort_month)
        except ValueError:
            continue
        retention = []
        for future_month in all_months[start_idx + 1 : start_idx + 7]:
            retained = sum(
                1
                for cid in cids
                if future_month in customer_order_months.get(cid, set())
            )
            retention.append(round(retained / size * 100, 1))
        cohorts.append(CohortRow(cohort=cohort_month, size=size, retention=retention))

    # CLV stats
    clv_values = sorted(cs["total_spent"] for cs in customer_stats.values())
    n = len(clv_values)
    avg_clv = sum(clv_values) // n
    median_clv = clv_values[n // 2]
    top_10_idx = max(0, int(n * 0.9))
    top_10_pct_clv = sum(clv_values[top_10_idx:]) // max(n - top_10_idx, 1)
    single_order = sum(1 for cs in customer_stats.values() if cs["orders"] == 1)
    single_order_pct = round(single_order / n * 100, 1)

    return SuccessResponse(
        data=CustomerSegmentsResponse(
            segments=segments,
            cohorts=cohorts,
            clv=CLVStats(
                avg_clv=avg_clv,
                median_clv=median_clv,
                top_10_pct_clv=top_10_pct_clv,
                total_customers=total_customers,
                single_order_pct=single_order_pct,
            ),
        ),
        message="Customer segments retrieved successfully",
    )


# ── Product Performance ──


class ProductPerformanceItem(BaseModel):
    id: str
    name: str
    sku: str | None
    image_url: str | None = None
    revenue: int  # cents
    quantity_sold: int
    current_stock: int
    revenue_trend: list[int]  # 7 data points (daily revenue, last 7 days)
    # Profit fields. All null if the product has no cost_price set.
    cost_price: int | None = None  # cents (current product cost)
    profit: int | None = None  # cents
    margin_percent: float | None = None


class CategoryPerformanceItem(BaseModel):
    category_id: str | None
    category_name: str
    revenue: int  # cents
    quantity_sold: int
    product_count: int


class InventoryHealthResponse(BaseModel):
    in_stock: int
    low_stock: int
    out_of_stock: int
    dead_stock: int  # stock > 0 but 0 sales in period


class ProductPerformanceResponse(BaseModel):
    products: list[ProductPerformanceItem]
    categories: list[CategoryPerformanceItem]
    inventory: InventoryHealthResponse


class AbcClassSummary(BaseModel):
    count: int
    revenue_cents: int
    revenue_share_pct: float


class AbcProductItem(BaseModel):
    product_id: str
    name: str
    abc_class: str  # "A" | "B" | "C"
    revenue_cents: int
    revenue_share_pct: float
    sell_through_pct: float


class DeadStockBucket(BaseModel):
    label: str  # "30-60" | "60-90" | "90+"
    products: int
    units: int
    value_cents: int


class DeadStockProductItem(BaseModel):
    product_id: str
    name: str
    quantity: int
    value_cents: int
    days_since_last_sale: int | None  # None = never sold in the horizon


class InventoryAnalyticsResponse(BaseModel):
    """Sell-through, ABC classification, and dead-stock aging.

    - Sell-through = units sold ÷ (units sold + units still in stock)
      over the selected window — the standard retail definition.
    - ABC: products ranked by window revenue; A = the head that makes
      up 80% of revenue, B = the next 15%, C = the tail (incl. products
      with zero sales).
    - Dead stock: in-stock products with no sale in ≥30 days, bucketed
      by age of last sale (never-sold products age from their creation
      date). Value uses cost_price when set, else sale price
      (``value_is_cost`` tells the UI which caption to show).
    """

    sell_through_pct: float
    units_sold: int
    units_in_stock: int
    abc: dict[str, AbcClassSummary]  # keys "A" | "B" | "C"
    abc_products: list[AbcProductItem]
    dead_stock_buckets: list[DeadStockBucket]
    dead_stock_products: list[DeadStockProductItem]
    dead_stock_value_cents: int
    value_is_cost: bool


_DEAD_STOCK_HORIZON_DAYS = 180


@router.get(
    "/inventory-analytics",
    response_model=SuccessResponse[InventoryAnalyticsResponse],
    summary="Get inventory analytics (sell-through, ABC, dead stock)",
    operation_id="get_inventory_analytics",
)
async def get_inventory_analytics(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Inventory health beyond restock suggestions — what to reorder
    (A), what to watch (B), and what to discount or retire (dead C)."""
    sales = await analytics_repo.product_sales_window(
        store.id, window.start, window.end
    )
    stock = await analytics_repo.product_stock_snapshot(store.id)
    horizon_start = window.end - timedelta(days=_DEAD_STOCK_HORIZON_DAYS)
    last_sold = await analytics_repo.product_last_sold(store.id, horizon_start)

    sales_by_pid = {s["product_id"]: s for s in sales}
    stock_by_pid = {p["product_id"]: p for p in stock}

    # ── Sell-through (store level) ─────────────────────────────────
    units_sold = sum(s["units_sold"] for s in sales)
    units_in_stock = sum(p["quantity"] for p in stock)
    denominator = units_sold + units_in_stock
    sell_through = (units_sold / denominator * 100) if denominator > 0 else 0.0

    # ── ABC classification on window revenue ───────────────────────
    total_revenue = sum(s["revenue_cents"] for s in sales)
    ranked = sorted(sales, key=lambda s: s["revenue_cents"], reverse=True)
    abc_class: dict[str, str] = {}
    cumulative = 0
    for s in ranked:
        # Classify by the cumulative share BEFORE this product: the item
        # that crosses the 80% boundary still belongs to the head (A).
        # Classifying on the after-share put a single product carrying
        # 98% of revenue into C — the exact opposite of its importance.
        share_before = cumulative / total_revenue if total_revenue > 0 else 1.0
        cumulative += s["revenue_cents"]
        abc_class[s["product_id"]] = (
            "A" if share_before < 0.80 else ("B" if share_before < 0.95 else "C")
        )
    # Zero-sales products are C by definition.
    for pid in stock_by_pid:
        abc_class.setdefault(pid, "C")

    abc_summary: dict[str, dict] = {
        k: {"count": 0, "revenue_cents": 0} for k in ("A", "B", "C")
    }
    for pid, cls in abc_class.items():
        abc_summary[cls]["count"] += 1
        abc_summary[cls]["revenue_cents"] += sales_by_pid.get(pid, {}).get(
            "revenue_cents", 0
        )

    abc_products = []
    for s in ranked[:50]:
        pid = s["product_id"]
        p = stock_by_pid.get(pid)
        st_den = s["units_sold"] + (p["quantity"] if p else 0)
        abc_products.append(
            AbcProductItem(
                product_id=pid,
                name=(p["name"] if p else "") or "",
                abc_class=abc_class[pid],
                revenue_cents=s["revenue_cents"],
                revenue_share_pct=round(s["revenue_cents"] / total_revenue * 100, 1)
                if total_revenue > 0
                else 0.0,
                sell_through_pct=round(s["units_sold"] / st_den * 100, 1)
                if st_den > 0
                else 0.0,
            )
        )

    # ── Dead stock aging ───────────────────────────────────────────
    now = window.end
    buckets = {"30-60": [0, 0, 0], "60-90": [0, 0, 0], "90+": [0, 0, 0]}
    dead_products: list[DeadStockProductItem] = []
    any_price_fallback = False
    for p in stock:
        if p["quantity"] <= 0:
            continue
        sold_at = last_sold.get(p["product_id"])
        if sold_at is not None:
            days = (now - sold_at).days
        else:
            # Never sold in the horizon — age from creation, capped at
            # the horizon so ancient catalogs don't overflow the label.
            created = p.get("created_at")
            days = (
                min((now - created).days, _DEAD_STOCK_HORIZON_DAYS)
                if created
                else _DEAD_STOCK_HORIZON_DAYS
            )
        if days < 30:
            continue
        label = "30-60" if days < 60 else ("60-90" if days < 90 else "90+")
        value = p["quantity"] * p["unit_value_cents"]
        if not p["value_is_cost"]:
            any_price_fallback = True
        buckets[label][0] += 1
        buckets[label][1] += p["quantity"]
        buckets[label][2] += value
        dead_products.append(
            DeadStockProductItem(
                product_id=p["product_id"],
                name=p["name"] or "",
                quantity=p["quantity"],
                value_cents=value,
                days_since_last_sale=(now - sold_at).days if sold_at else None,
            )
        )

    dead_products.sort(key=lambda d: d.value_cents, reverse=True)

    return SuccessResponse(
        data=InventoryAnalyticsResponse(
            sell_through_pct=round(sell_through, 1),
            units_sold=units_sold,
            units_in_stock=units_in_stock,
            abc={
                k: AbcClassSummary(
                    count=v["count"],
                    revenue_cents=v["revenue_cents"],
                    revenue_share_pct=round(v["revenue_cents"] / total_revenue * 100, 1)
                    if total_revenue > 0
                    else 0.0,
                )
                for k, v in abc_summary.items()
            },
            abc_products=abc_products,
            dead_stock_buckets=[
                DeadStockBucket(label=k, products=v[0], units=v[1], value_cents=v[2])
                for k, v in buckets.items()
            ],
            dead_stock_products=dead_products[:20],
            dead_stock_value_cents=sum(v[2] for v in buckets.values()),
            value_is_cost=not any_price_fallback,
        ),
        message="Inventory analytics retrieved successfully",
    )


@router.get(
    "/product-performance",
    response_model=SuccessResponse[ProductPerformanceResponse],
    summary="Get product performance analytics",
    operation_id="get_product_performance",
)
async def get_product_performance(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
    sort_by: str = Query("revenue", description="Sort by: revenue, quantity, name"),
):
    """Get product-level performance, category breakdown, and inventory health.

    Three SQL aggregations replace the previous 5000-order in-memory
    loop: ``top_products`` (line_items unnest + GROUP BY), the optional
    7-day daily trend per top product, and inventory bucket counts.
    Catalog metadata (cost_price, category) is joined via a single
    product fetch limited to the active SKUs.
    """
    period_start = window.start
    now = window.end
    seven_days_ago = now - timedelta(days=7)

    # Top 50 products in the period (paid only, line_items unnested in SQL)
    top = await analytics_repo.top_products(store.id, period_start, now, limit=50)
    top_ids = [t["product_id"] for t in top]

    # 7-day per-product trend, scoped to those top products only
    daily = (
        await analytics_repo.daily_revenue_per_product(
            store.id, seven_days_ago, now, product_ids=top_ids
        )
        if top_ids
        else {}
    )
    trend_dates = [(now - timedelta(days=6 - i)).strftime("%Y-%m-%d") for i in range(7)]

    # Catalog metadata for stock + category. We still fetch the catalog
    # because inventory health needs it; the loop is bounded by SKU count.
    products = await product_repo.get_by_store(store.id, skip=0, limit=5000)
    product_map = {str(p.id): p for p in products}

    # Resolve category names up front (the Product entity carries only
    # category_id, no joined category) so the breakdown shows real names
    # instead of a "Category <uuid>" fallback.
    categories_list = await category_repo.get_by_store(
        store.id, skip=0, limit=1000, include_inactive=True
    )
    category_name_map = {str(c.id): c.name for c in categories_list}

    # Optional client-side sort over the SQL-aggregated top set.
    sort_key = {
        "revenue": lambda r: r["revenue_cents"],
        "quantity": lambda r: r["units_sold"],
        "name": lambda r: (r.get("product_name") or "").lower(),
    }.get(sort_by, lambda r: r["revenue_cents"])
    reverse = sort_by != "name"
    top_sorted = sorted(top, key=sort_key, reverse=reverse)

    product_items: list[ProductPerformanceItem] = []
    for r in top_sorted:
        pid = r["product_id"]
        p = product_map.get(pid)
        stock = p.quantity if p else 0
        trend = [daily.get(pid, {}).get(d, 0) for d in trend_dates]

        cost_cents: int | None = None
        profit_cents: int | None = None
        margin_pct: float | None = None
        if p is not None and p.cost_price is not None:
            cost_cents = p.cost_price.cents
            profit_cents = r["revenue_cents"] - (cost_cents * r["units_sold"])
            if r["revenue_cents"] > 0:
                margin_pct = round(profit_cents / r["revenue_cents"] * 100, 1)

        product_items.append(
            ProductPerformanceItem(
                id=pid,
                name=r["product_name"] or (p.name if p else "Unknown"),
                sku=p.sku if p else None,
                image_url=(p.images[0] if p and p.images else None),
                revenue=r["revenue_cents"],
                quantity_sold=r["units_sold"],
                current_stock=stock,
                revenue_trend=trend,
                cost_price=cost_cents,
                profit=profit_cents,
                margin_percent=margin_pct,
            )
        )

    # Category aggregation: sum the top set by category. Cheap — bounded
    # by `limit` (50) so no SQL roundtrip needed.
    category_data: dict[str | None, dict] = {}
    for r in top:
        pid = r["product_id"]
        p = product_map.get(pid)
        cat_id = str(p.category_id) if p and p.category_id else None
        cat_name = "Uncategorized"
        if cat_id:
            # Real name from the catalog; fall back to a short id only if the
            # category was deleted but still referenced by an order line.
            cat_name = category_name_map.get(cat_id, f"Category {cat_id[:8]}")

        if cat_id not in category_data:
            category_data[cat_id] = {
                "name": cat_name,
                "revenue": 0,
                "quantity": 0,
                "products": set(),
            }
        category_data[cat_id]["revenue"] += r["revenue_cents"]
        category_data[cat_id]["quantity"] += r["units_sold"]
        category_data[cat_id]["products"].add(pid)

    categories = [
        CategoryPerformanceItem(
            category_id=cat_id,
            category_name=d["name"],
            revenue=d["revenue"],
            quantity_sold=d["quantity"],
            product_count=len(d["products"]),
        )
        for cat_id, d in sorted(
            category_data.items(), key=lambda x: x[1]["revenue"], reverse=True
        )
    ]

    # Inventory health: in/low/out from a single SQL aggregation; dead
    # stock is computed against the sold-set in Python (bounded by
    # catalog size).
    inv = await analytics_repo.inventory_health(store.id)
    sold_product_ids = set(top_ids)
    dead_stock = sum(
        1 for p in products if p.quantity > 0 and str(p.id) not in sold_product_ids
    )

    return SuccessResponse(
        data=ProductPerformanceResponse(
            products=product_items,
            categories=categories,
            inventory=InventoryHealthResponse(
                in_stock=inv["in_stock"],
                low_stock=inv["low_stock"],
                out_of_stock=inv["out_of_stock"],
                dead_stock=dead_stock,
            ),
        ),
        message="Product performance retrieved successfully",
    )


# ── Conversion Funnel ──

FUNNEL_STEPS = [
    "page_view",
    "product_view",
    "add_to_cart",
    "checkout_started",
    "order_completed",
    "order_delivered",
]


class FunnelStepResponse(BaseModel):
    step: str
    count: int
    drop_off_pct: float  # percentage drop from previous step


class FunnelTrendPoint(BaseModel):
    date: str
    conversion_rate: float  # daily overall conversion %


class StepTimingResponse(BaseModel):
    from_step: str
    to_step: str
    avg_minutes: float


class CartAbandonmentResponse(BaseModel):
    carts_created: int
    checkouts_started: int
    abandonment_rate: float  # percentage
    estimated_lost_revenue: int  # cents


class FunnelResponse(BaseModel):
    steps: list[FunnelStepResponse]
    overall_conversion_pct: float
    trend: list[FunnelTrendPoint]
    step_timings: list[StepTimingResponse]
    cart_abandonment: CartAbandonmentResponse


class SearchTermItem(BaseModel):
    term: str
    searches: int
    sessions: int  # distinct fingerprints that searched this term
    zero_result_searches: int  # 0 until storefront reports results_count


class SearchTermsResponse(BaseModel):
    """Storefront search analytics — data exists since the /track
    step-whitelist fix (before it, search events were collapsed to
    page_view and the query strings were lost)."""

    total_searches: int
    unique_search_sessions: int
    # % of searching sessions that also completed an order in-window.
    # Session co-occurrence, not attribution.
    search_conversion_rate: float
    terms: list[SearchTermItem]


@router.get(
    "/search-terms",
    response_model=SuccessResponse[SearchTermsResponse],
    summary="Get top storefront search terms",
    operation_id="get_search_terms",
)
async def get_search_terms(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """Top search terms, volumes, and search→purchase session conversion."""
    summary = await analytics_repo.search_summary(store.id, window.start, window.end)
    terms = await analytics_repo.search_terms(
        store.id, window.start, window.end, limit=limit
    )

    sessions = summary["unique_sessions"]
    conversion = (
        (summary["converted_sessions"] / sessions) * 100 if sessions > 0 else 0.0
    )

    return SuccessResponse(
        data=SearchTermsResponse(
            total_searches=summary["total_searches"],
            unique_search_sessions=sessions,
            search_conversion_rate=round(conversion, 1),
            terms=[SearchTermItem(**t) for t in terms],
        ),
        message="Search terms retrieved successfully",
    )


# ── Customer health (AI-3) ──────────────────────────────────────────


class HealthStateCount(BaseModel):
    state: str
    count: int


class CustomerHealthItem(BaseModel):
    customer_id: UUID
    name: str
    score: int
    state: str
    orders: int
    total_spent_cents: int
    days_since_last: int


class CustomerHealthResponse(BaseModel):
    distribution: list[HealthStateCount]
    median_gap_days: float
    customers: list[CustomerHealthItem]


@router.get(
    "/customer-health",
    response_model=SuccessResponse[CustomerHealthResponse],
    summary="Customer health scores + states",
    operation_id="get_customer_health",
)
async def get_customer_health(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    state: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """0–100 health per customer + lifecycle state (VIP … churned),
    computed live over 365d of orders. Recency decays on the STORE's own
    repurchase rhythm, and the monetary quintile finally participates
    (the legacy RFM segmenter ignored it — D14)."""
    from src.application.services.customer_health_service import (
        median_interpurchase_gap,
        score_store_customers,
    )
    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.database.models.tenant.funnel_event import (
        FunnelEventModel,
    )
    from src.infrastructure.database.models.tenant.order import OrderModel

    now = datetime.now(UTC)
    since = now - timedelta(days=365)
    non_revenue = (OrderStatus.CANCELLED, OrderStatus.REFUNDED)
    ninety = now - timedelta(days=90)
    one80 = now - timedelta(days=180)

    q = (
        select(
            OrderModel.customer_id,
            func.count(OrderModel.id).label("orders"),
            func.coalesce(func.sum(OrderModel.total), 0).label("spent"),
            func.min(OrderModel.created_at).label("first_at"),
            func.max(OrderModel.created_at).label("last_at"),
            func.count().filter(OrderModel.coupon_code.isnot(None)).label("couponed"),
            func.count()
            .filter(OrderModel.status == OrderStatus.RETURNED)
            .label("returned"),
            func.coalesce(
                func.sum(OrderModel.total).filter(OrderModel.created_at >= ninety), 0
            ).label("last90"),
            func.coalesce(
                func.sum(OrderModel.total).filter(
                    OrderModel.created_at >= one80, OrderModel.created_at < ninety
                ),
                0,
            ).label("prior90"),
        )
        .where(
            OrderModel.store_id == store.id,
            OrderModel.created_at >= since,
            OrderModel.customer_id.isnot(None),
            OrderModel.status.notin_(non_revenue),
        )
        .group_by(OrderModel.customer_id)
    )
    rows_db = (await db.execute(q)).all()
    if not rows_db:
        return SuccessResponse(
            data=CustomerHealthResponse(
                distribution=[], median_gap_days=30.0, customers=[]
            ),
            message="No customers yet",
        )

    ids = [r.customer_id for r in rows_db]
    eng_q = (
        select(
            FunnelEventModel.customer_id,
            func.count().label("events"),
        )
        .where(
            FunnelEventModel.store_id == store.id,
            FunnelEventModel.customer_id.in_(ids),
            FunnelEventModel.created_at >= now - timedelta(days=30),
        )
        .group_by(FunnelEventModel.customer_id)
    )
    events = {r.customer_id: int(r.events) for r in (await db.execute(eng_q)).all()}

    names_q = select(
        CustomerModel.id, CustomerModel.first_name, CustomerModel.last_name
    ).where(CustomerModel.id.in_(ids))
    names = {
        r.id: f"{r.first_name} {r.last_name}".strip()
        for r in (await db.execute(names_q)).all()
    }

    rows = [
        {
            "customer_id": r.customer_id,
            "orders": int(r.orders),
            "total_spent_cents": int(r.spent),
            "first_at": r.first_at,
            "last_at": r.last_at,
            "coupon_orders": int(r.couponed),
            "returned_orders": int(r.returned),
            "spend_last_90": int(r.last90),
            "spend_prior_90": int(r.prior90),
            "events_30d": events.get(r.customer_id, 0),
        }
        for r in rows_db
    ]
    total_orders = sum(r["orders"] for r in rows)
    store_aov = (
        sum(r["total_spent_cents"] for r in rows) // total_orders if total_orders else 0
    )

    scored = score_store_customers(rows, now, store_aov)
    dist: dict[str, int] = {}
    for s in scored:
        dist[s["state"]] = dist.get(s["state"], 0) + 1

    filtered = [s for s in scored if state is None or s["state"] == state]
    return SuccessResponse(
        data=CustomerHealthResponse(
            distribution=[
                HealthStateCount(state=k, count=v)
                for k, v in sorted(dist.items(), key=lambda x: -x[1])
            ],
            median_gap_days=round(median_interpurchase_gap(rows), 1),
            customers=[
                CustomerHealthItem(
                    customer_id=s["customer_id"],
                    name=names.get(s["customer_id"], ""),
                    score=s["score"],
                    state=s["state"],
                    orders=s["orders"],
                    total_spent_cents=s["total_spent_cents"],
                    days_since_last=int((now - s["last_at"]).total_seconds() // 86400)
                    if s["last_at"]
                    else 0,
                )
                for s in filtered[:limit]
            ],
        ),
        message="Customer health computed",
    )


# ── Advisor signals (AI Commerce Intelligence) ──────────────────────


class SignalItem(BaseModel):
    id: UUID
    kind: str  # advice | opportunity | alert
    rule_id: str
    severity: str  # critical | warning | opportunity | info
    title: str
    action: str
    expected_impact_cents: int | None
    created_at: str


class SignalsResponse(BaseModel):
    signals: list[SignalItem]


@router.get(
    "/signals",
    response_model=SuccessResponse[SignalsResponse],
    summary="Get active advisor signals",
    operation_id="get_signals",
)
async def get_signals(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated["AsyncSession", Depends(get_db)],
    lang: Annotated[Literal["en", "ar"], Query()] = "en",
):
    """Active advice/opportunities for this store, ranked by expected
    EGP impact. Copy is rendered from the rule templates at read time."""
    from src.application.services.advisor_rules import render_signal
    from src.application.services.alert_service import render_alert
    from src.infrastructure.database.models.tenant.merchant_signal import (
        MerchantSignalModel,
    )

    rows = (
        (
            await db.execute(
                select(MerchantSignalModel)
                .where(
                    MerchantSignalModel.store_id == store.id,
                    MerchantSignalModel.status == "active",
                )
                .order_by(MerchantSignalModel.expected_impact_cents.desc().nulls_last())
            )
        )
        .scalars()
        .all()
    )
    signals = []
    for r in rows:
        # AL-* rows come from the hourly alert engine; everything else
        # from the nightly advisor. Each owns its template registry.
        renderer = render_alert if r.rule_id.startswith("AL-") else render_signal
        rendered = renderer(r.rule_id, r.metrics_snapshot or {}, lang)
        signals.append(
            SignalItem(
                id=r.id,
                kind=r.kind,
                rule_id=r.rule_id,
                severity=r.severity,
                title=rendered["title"],
                action=rendered["action"],
                expected_impact_cents=r.expected_impact_cents,
                created_at=r.created_at.isoformat(),
            )
        )
    return SuccessResponse(
        data=SignalsResponse(signals=signals),
        message="Signals retrieved",
    )


@router.post(
    "/signals/{signal_id}/dismiss",
    response_model=SuccessResponse[dict],
    summary="Dismiss an advisor signal",
    operation_id="dismiss_signal",
)
async def dismiss_signal(
    signal_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated["AsyncSession", Depends(get_db)],
):
    """Hide a signal the merchant doesn't want to act on. It won't
    re-notify while its cooldown holds; a later re-fire creates a fresh
    signal if the condition persists."""
    from src.infrastructure.database.models.tenant.merchant_signal import (
        MerchantSignalModel,
    )

    row = (
        await db.execute(
            select(MerchantSignalModel).where(
                MerchantSignalModel.id == signal_id,
                MerchantSignalModel.store_id == store.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    row.status = "dismissed"
    await db.commit()
    return SuccessResponse(data={"dismissed": True}, message="Signal dismissed")


@router.post(
    "/signals/refresh",
    response_model=SuccessResponse[dict],
    summary="Re-run the advisor rules for this store now",
    operation_id="refresh_signals",
)
async def refresh_signals(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated["AsyncSession", Depends(get_db)],
):
    """On-demand rule evaluation (the nightly sweep does this
    automatically). Synchronous — a store's rule pass is ~10 queries."""
    from src.application.services.alert_service import run_store_alerts
    from src.application.services.intelligence_service import run_store

    tz_name = resolve_store_timezone_name(store.settings)
    currency = store.default_currency.value if store.default_currency else "EGP"
    out = await run_store(db, store.id, store.tenant_id, tz_name, currency)
    alerts = await run_store_alerts(db, store.id, store.tenant_id, tz_name, currency)
    await db.commit()
    return SuccessResponse(
        data={"advisor": out, "alerts": alerts}, message="Signals refreshed"
    )


# ── Custom report builder ───────────────────────────────────────────


class ReportRow(BaseModel):
    label: str
    revenue_cents: int
    orders: int
    aov_cents: int
    units: int


class ReportBuilderResponse(BaseModel):
    dimension: str
    rows: list[ReportRow]
    totals: ReportRow


_REPORT_DIMENSION = Literal[
    "day",
    "week",
    "month",
    "payment_method",
    "governorate",
    "channel",
    "coupon",
    "product",
]


@router.get(
    "/report-builder",
    response_model=SuccessResponse[ReportBuilderResponse],
    summary="Custom report: metrics grouped by a chosen dimension",
    operation_id="get_report_builder",
)
async def get_report_builder(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
    dimension: Annotated[_REPORT_DIMENSION, Query()] = "day",
):
    """Group revenue / orders / AOV (and units, for products) by any
    supported dimension — the merchant-facing custom report builder.
    Metric selection happens client-side from the columns returned here,
    so switching metric doesn't re-query."""
    rows = await analytics_repo.run_report(
        store.id, dimension, window.start, window.end, tz=window.tz
    )
    total_rev = sum(r["revenue_cents"] for r in rows)
    total_orders = sum(r["orders"] for r in rows)
    total_units = sum(r["units"] for r in rows)
    return SuccessResponse(
        data=ReportBuilderResponse(
            dimension=dimension,
            rows=[ReportRow(**r) for r in rows],
            totals=ReportRow(
                label="Total",
                revenue_cents=total_rev,
                orders=total_orders,
                aov_cents=total_rev // total_orders if total_orders else 0,
                units=total_units,
            ),
        ),
        message="Report generated",
    )


# ── Weekly digest (preview + test send) ─────────────────────────────


class DigestContent(BaseModel):
    headline: str
    highlights: list[str]
    has_sales: bool
    period_start: str
    period_end: str


async def _compute_weekly_digest(
    store: Store,
    rollup_repo: AnalyticsRollupRepository,
    analytics_repo: AnalyticsRepository,
    lang: str,
) -> DigestContent:
    """Assemble this-week metrics (store-local) and build the digest."""
    from src.application.services.analytics_digest_service import build_weekly_digest

    tz_name = resolve_store_timezone_name(store.settings)
    today = datetime.now(UTC).astimezone(safe_zone(tz_name)).date()
    week_start = today - timedelta(days=6)  # 7-day window incl. today
    prev_end = week_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)

    cur = await rollup_repo.get_aggregated(store.id, week_start, today)
    prev = await rollup_repo.get_aggregated(store.id, prev_start, prev_end)

    # Top product across the week (rollup JSONB merge).
    rollups = await rollup_repo.get_range(store.id, week_start, today)
    prod_units: dict[str, tuple[str, int]] = {}
    for r in rollups or []:
        for item in r.top_products_json or []:
            pid = str(item.get("product_id", ""))
            if not pid:
                continue
            name, units = prod_units.get(pid, (item.get("name", ""), 0))
            prod_units[pid] = (
                name or item.get("name", ""),
                units + int(item.get("quantity", 0) or 0),
            )
    top = max(prod_units.values(), key=lambda x: x[1], default=None)

    orders = int(cur["total_orders"])
    revenue = int(cur["total_revenue_cents"])
    currency = store.default_currency.value if store.default_currency else "EGP"

    def _fmt(cents: int) -> str:
        return f"{cents / 100:,.2f} {currency}"

    metrics = {
        "revenue_cents": revenue,
        "prev_revenue_cents": int(prev["total_revenue_cents"]),
        "orders": orders,
        "prev_orders": int(prev["total_orders"]),
        "new_customers": int(cur.get("new_customers", 0) or 0),
        "aov_cents": revenue // orders if orders > 0 else 0,
        "top_product_name": top[0] if top else None,
        "top_product_units": top[1] if top else 0,
    }
    built = build_weekly_digest(metrics, lang, _fmt)
    return DigestContent(
        headline=built["headline"],
        highlights=built["highlights"],
        has_sales=built["has_sales"],
        period_start=week_start.isoformat(),
        period_end=today.isoformat(),
    )


class DigestSettings(BaseModel):
    enabled: bool = False
    channels: list[Literal["email", "whatsapp"]] = ["email"]


class PutDigestSettingsRequest(BaseModel):
    enabled: bool
    channels: list[Literal["email", "whatsapp"]] = Field(min_length=1, max_length=2)


@router.get(
    "/digest/settings",
    response_model=SuccessResponse[DigestSettings],
    summary="Get weekly-digest preferences",
    operation_id="get_digest_settings",
)
async def get_digest_settings(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """The store's weekly-digest opt-in (defaults off, email channel)."""
    cfg = ((store.settings or {}).get("analytics_digest")) or {}
    return SuccessResponse(
        data=DigestSettings(
            enabled=bool(cfg.get("enabled", False)),
            channels=cfg.get("channels") or ["email"],
        ),
        message="Digest settings retrieved",
    )


@router.put(
    "/digest/settings",
    response_model=SuccessResponse[DigestSettings],
    summary="Set weekly-digest preferences",
    operation_id="put_digest_settings",
)
async def put_digest_settings(
    body: PutDigestSettingsRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Enable/disable the weekly digest and pick channels."""
    settings = dict(store.settings or {})
    settings["analytics_digest"] = {
        "enabled": body.enabled,
        "channels": body.channels,
    }
    store.settings = settings
    await store_repo.update(store)
    return SuccessResponse(
        data=DigestSettings(enabled=body.enabled, channels=body.channels),
        message="Digest settings saved",
    )


@router.get(
    "/digest/preview",
    response_model=SuccessResponse[DigestContent],
    summary="Preview the weekly analytics digest",
    operation_id="get_digest_preview",
)
async def get_digest_preview(
    store: Annotated[Store, Depends(verify_store_ownership)],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    lang: Annotated[Literal["en", "ar"], Query()] = "en",
):
    """The exact recap the merchant would receive by email/WhatsApp — so
    they can see it before enabling scheduled sends."""
    content = await _compute_weekly_digest(store, rollup_repo, analytics_repo, lang)
    return SuccessResponse(data=content, message="Digest preview generated")


# ── Chart annotations (store events) ────────────────────────────────


class AnnotationItem(BaseModel):
    # `date` matches the sales-chart day label ("Jul 14") so the UI can
    # pin a marker to the right bucket without re-parsing dates.
    date: str
    iso_date: str  # store-local YYYY-MM-DD
    type: str  # product | coupon | campaign | theme
    label: str


class AnnotationsResponse(BaseModel):
    annotations: list[AnnotationItem]


@router.get(
    "/annotations",
    response_model=SuccessResponse[AnnotationsResponse],
    summary="Get store-event annotations for charts",
    operation_id="get_annotations",
)
async def get_annotations(
    store: Annotated[Store, Depends(verify_store_ownership)],
    annotation_repo: Annotated[
        "AnnotationRepository", Depends(get_annotation_repository)
    ],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Store events (product/coupon/campaign/theme) in the window, as
    chart markers — the merchant sees WHAT caused a spike or dip. Dates
    are labeled to match the sales chart's day buckets on the store's
    wall clock."""
    events = await annotation_repo.get_events(store.id, window.start, window.end)
    zone = safe_zone(window.tz)

    out: list[AnnotationItem] = []
    for e in events:
        at = e["at"]
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        local = at.astimezone(zone)
        out.append(
            AnnotationItem(
                date=local.strftime("%b %d"),
                iso_date=local.date().isoformat(),
                type=e["type"],
                label=e["label"],
            )
        )
    return SuccessResponse(
        data=AnnotationsResponse(annotations=out),
        message="Annotations retrieved successfully",
    )


# ── Metric targets (goals + pace) ───────────────────────────────────


class MetricTargetProgress(BaseModel):
    """One goal with its live progress and pace.

    ``target_value``/``actual_value``/``projected_value`` are integers in
    the metric's smallest unit: cents (revenue, aov), count (orders), or
    basis points (conversion — 250 = 2.5%).
    """

    metric: str
    period: str
    target_value: int
    actual_value: int
    progress_pct: float
    expected_pct: float  # share of the period already elapsed
    pace: str  # "ahead" | "on_track" | "behind"
    projected_value: int  # linear run-rate projection to period end
    days_elapsed: int
    days_remaining: int
    days_total: int
    period_start: str  # ISO date, store-local
    period_end: str


class MetricTargetsResponse(BaseModel):
    targets: list[MetricTargetProgress]


class MetricTargetInput(BaseModel):
    metric: Literal["revenue", "orders", "aov", "conversion"]
    period: Literal["month", "quarter"] = "month"
    # 0 = stop tracking this goal (deletes the row).
    target_value: int = Field(ge=0, le=10**15)


class PutMetricTargetsRequest(BaseModel):
    targets: list[MetricTargetInput] = Field(min_length=1, max_length=8)


def _period_bounds(now_local: datetime, period: str) -> tuple[date, date]:
    """Store-local [start, end) calendar bounds of the current period."""
    if period == "quarter":
        q_start_month = ((now_local.month - 1) // 3) * 3 + 1
        start = date(now_local.year, q_start_month, 1)
        if q_start_month == 10:
            end = date(now_local.year + 1, 1, 1)
        else:
            end = date(now_local.year, q_start_month + 3, 1)
    else:
        start = date(now_local.year, now_local.month, 1)
        if now_local.month == 12:
            end = date(now_local.year + 1, 1, 1)
        else:
            end = date(now_local.year, now_local.month + 1, 1)
    return start, end


@router.get(
    "/targets",
    response_model=SuccessResponse[MetricTargetsResponse],
    summary="Get metric targets with live pace",
    operation_id="get_metric_targets",
)
async def get_metric_targets(
    store: Annotated[Store, Depends(verify_store_ownership)],
    target_repo: Annotated[
        "MetricTargetRepository", Depends(get_metric_target_repository)
    ],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
):
    """Each stored goal + actual-to-date, elapsed pace, and a linear
    run-rate projection — the Shopify-style target gauge, computed on
    the store's wall clock so "this month" means the merchant's month."""
    rows = await target_repo.get_for_store(store.id)
    if not rows:
        return SuccessResponse(
            data=MetricTargetsResponse(targets=[]),
            message="No targets set",
        )

    tz_name = resolve_store_timezone_name(store.settings)
    now_local = datetime.now(UTC).astimezone(safe_zone(tz_name))

    out: list[MetricTargetProgress] = []
    # Group by period so each period's aggregates are fetched once even
    # with all four metrics configured.
    for period in {r.period for r in rows}:
        start_d, end_d = _period_bounds(now_local, period)
        days_total = (end_d - start_d).days
        days_elapsed = min((now_local.date() - start_d).days + 1, days_total)
        days_remaining = max(days_total - days_elapsed, 0)

        agg = await rollup_repo.get_aggregated(store.id, start_d, now_local.date())
        revenue = int(agg["total_revenue_cents"])
        orders = int(agg["total_orders"])

        start_instant, _ = local_day_bounds(start_d, tz_name)
        # Rollup-empty fallback (new store / cron hasn't run) — same
        # convention as /overview: live aggregation over orders.
        if revenue == 0 and orders == 0:
            now_utc = datetime.now(UTC)
            revenue = await order_repo.get_revenue_by_date_range(
                store.id, start_instant, now_utc
            )
            orders = await order_repo.count_by_store(
                store.id, date_from=start_instant, date_to=now_utc
            )
        visitors = await pv_repo.count_unique_visitors(
            store.id, start_instant, datetime.now(UTC)
        )

        actuals = {
            "revenue": revenue,
            "orders": orders,
            "aov": revenue // orders if orders > 0 else 0,
            # Basis points; conversion is orders per unique visitor.
            "conversion": round(orders / visitors * 10_000) if visitors > 0 else 0,
        }

        expected_pct = round(days_elapsed / days_total * 100, 1)
        for r in (x for x in rows if x.period == period):
            actual = actuals.get(r.metric, 0)
            progress = (
                round(actual / r.target_value * 100, 1) if r.target_value > 0 else 0.0
            )
            # Ratio metrics (aov, conversion) don't accumulate over the
            # period — pace compares them to the target directly, and
            # the linear projection only applies to volume metrics.
            is_ratio = r.metric in ("aov", "conversion")
            if is_ratio:
                pace = (
                    "ahead"
                    if progress >= 105
                    else ("behind" if progress < 95 else "on_track")
                )
                projected = actual
            else:
                pace = (
                    "ahead"
                    if progress >= expected_pct + 5
                    else ("behind" if progress < expected_pct - 5 else "on_track")
                )
                projected = (
                    round(actual / days_elapsed * days_total) if days_elapsed > 0 else 0
                )
            out.append(
                MetricTargetProgress(
                    metric=r.metric,
                    period=r.period,
                    target_value=r.target_value,
                    actual_value=actual,
                    progress_pct=progress,
                    expected_pct=expected_pct,
                    pace=pace,
                    projected_value=projected,
                    days_elapsed=days_elapsed,
                    days_remaining=days_remaining,
                    days_total=days_total,
                    period_start=start_d.isoformat(),
                    period_end=(end_d - timedelta(days=1)).isoformat(),
                )
            )

    order_key = {"revenue": 0, "orders": 1, "aov": 2, "conversion": 3}
    out.sort(key=lambda t: (t.period, order_key.get(t.metric, 9)))
    return SuccessResponse(
        data=MetricTargetsResponse(targets=out),
        message="Metric targets retrieved successfully",
    )


@router.put(
    "/targets",
    response_model=SuccessResponse[dict],
    summary="Set metric targets",
    operation_id="put_metric_targets",
)
async def put_metric_targets(
    body: PutMetricTargetsRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    target_repo: Annotated[
        "MetricTargetRepository", Depends(get_metric_target_repository)
    ],
):
    """Upsert goals; a target_value of 0 removes that goal."""
    for t in body.targets:
        if t.target_value == 0:
            await target_repo.remove(store.id, t.metric, t.period)
        else:
            await target_repo.upsert(
                tenant_id=store.tenant_id,
                store_id=store.id,
                metric=t.metric,
                period=t.period,
                target_value=t.target_value,
            )
    await target_repo.session.commit()
    return SuccessResponse(
        data={"updated": len(body.targets)},
        message="Metric targets saved successfully",
    )


@router.get(
    "/funnel",
    response_model=SuccessResponse[FunnelResponse],
    summary="Get conversion funnel",
    operation_id="get_funnel",
)
async def get_funnel(
    store: Annotated[Store, Depends(verify_store_ownership)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get conversion funnel step counts with drop-off percentages."""
    period_start = window.start
    now = window.end

    counts = await funnel_repo.get_funnel_counts(store.id, period_start, now)

    steps = []
    prev_count = 0
    for i, step_name in enumerate(FUNNEL_STEPS):
        count = counts.get(step_name, 0)
        if i == 0:
            drop_off = 0.0
        else:
            drop_off = (
                round((1 - count / prev_count) * 100, 1) if prev_count > 0 else 0.0
            )
        steps.append(
            FunnelStepResponse(step=step_name, count=count, drop_off_pct=drop_off)
        )
        prev_count = count if count > 0 else prev_count

    first_count = steps[0].count if steps else 0
    last_count = steps[-1].count if steps else 0
    overall = round(last_count / first_count * 100, 2) if first_count > 0 else 0.0

    # Daily conversion trend
    daily_data = await funnel_repo.get_daily_funnel_counts(
        store.id,
        period_start,
        now,
        steps=["page_view", "order_completed"],
    )
    # Group by day
    day_views: dict[str, int] = {}
    day_orders: dict[str, int] = {}
    for row in daily_data:
        if row["step"] == "page_view":
            day_views[row["day"]] = row["count"]
        elif row["step"] == "order_completed":
            day_orders[row["day"]] = row["count"]

    all_days = sorted(set(list(day_views.keys()) + list(day_orders.keys())))
    trend = [
        FunnelTrendPoint(
            date=d,
            conversion_rate=round(day_orders.get(d, 0) / day_views[d] * 100, 2)
            if day_views.get(d, 0) > 0
            else 0.0,
        )
        for d in all_days
    ]

    # Step-pair timings
    timing_pairs = [
        ("add_to_cart", "checkout_started"),
        ("checkout_started", "order_completed"),
        ("page_view", "add_to_cart"),
    ]
    step_timings = []
    for from_s, to_s in timing_pairs:
        avg_min = await funnel_repo.get_step_pair_avg_minutes(
            store.id, period_start, now, from_s, to_s
        )
        if avg_min is not None:
            step_timings.append(
                StepTimingResponse(
                    from_step=from_s, to_step=to_s, avg_minutes=round(avg_min, 1)
                )
            )

    # Cart abandonment (real calculation)
    carts_created = counts.get("add_to_cart", 0)
    checkouts_started = counts.get("checkout_started", 0)
    abandonment_rate = (
        round((1 - checkouts_started / carts_created) * 100, 1)
        if carts_created > 0
        else 0.0
    )

    # Estimate lost revenue: avg order value * abandoned carts
    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=1000)
    paid_orders = [
        o
        for o in orders
        if o.payment_status in [PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED]
    ]
    avg_order_value = (
        sum(o.total for o in paid_orders) // len(paid_orders) if paid_orders else 0
    )
    abandoned_carts = max(carts_created - checkouts_started, 0)
    estimated_lost = abandoned_carts * avg_order_value

    cart_abandonment = CartAbandonmentResponse(
        carts_created=carts_created,
        checkouts_started=checkouts_started,
        abandonment_rate=abandonment_rate,
        estimated_lost_revenue=estimated_lost,
    )

    return SuccessResponse(
        data=FunnelResponse(
            steps=steps,
            overall_conversion_pct=overall,
            trend=trend,
            step_timings=step_timings,
            cart_abandonment=cart_abandonment,
        ),
        message="Funnel data retrieved successfully",
    )


# ── Marketing Attribution ──


class ChannelAttributionItem(BaseModel):
    channel: str
    visits: int
    orders: int
    revenue: int  # cents
    conversion_rate: float


class CampaignItem(BaseModel):
    campaign: str
    visits: int
    orders: int
    revenue: int  # cents


class LandingPageItem(BaseModel):
    path: str
    sessions: int


class ReferrerItem(BaseModel):
    host: str
    sessions: int


class MarketingAttributionResponse(BaseModel):
    channels: list[ChannelAttributionItem]
    campaigns: list[CampaignItem]
    total_visits: int
    attributed_visits: int  # visits with UTM data
    # Acquisition detail (real session data): where sessions ENTER the
    # store and which external hosts send them.
    top_landing_pages: list[LandingPageItem] = []
    top_referrers: list[ReferrerItem] = []


def _classify_channel(source: str | None, medium: str | None) -> str:
    """Classify a visit into a marketing channel based on UTM parameters."""
    if not source or source == "direct":
        return "Direct"
    source_lower = (source or "").lower()
    medium_lower = (medium or "").lower()
    if medium_lower in ("cpc", "ppc", "paid", "ad"):
        return "Paid"
    if source_lower in (
        "facebook",
        "instagram",
        "tiktok",
        "twitter",
        "x",
        "snapchat",
        "linkedin",
    ):
        return "Social"
    if medium_lower == "email":
        return "Email"
    if medium_lower == "referral" or source_lower != "direct":
        return "Referral"
    return "Organic"


@router.get(
    "/marketing-attribution",
    response_model=SuccessResponse[MarketingAttributionResponse],
    summary="Get marketing attribution analytics",
    operation_id="get_marketing_attribution",
)
async def get_marketing_attribution(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
):
    """Get marketing channel attribution from order UTM data and page views.

    Channel classification (Direct / Paid / Social / Email / Referral /
    Organic) is encoded as a SQL ``CASE`` over ``utm_source`` /
    ``utm_medium`` so we don't iterate orders. Campaigns come from a
    second aggregation with normalized (lower/trim) casing.
    """
    period_start = window.start
    now = window.end

    total_visits = await pv_repo.count_unique_visitors(store.id, period_start, now)
    channel_rows, attributed_visits = await analytics_repo.channel_attribution(
        store.id, period_start, now
    )
    campaign_rows = await analytics_repo.campaign_attribution(
        store.id, period_start, now, limit=20
    )
    # MEASURED sessions per channel/campaign from funnel_events UTM.
    # The previous implementation fabricated per-channel visits by
    # prorating total visitors by each channel's ORDER share, which
    # made conversion_rate quasi-circular and invented traffic for
    # channels with orders but no real visits. Channels with orders
    # and 0 tracked sessions (history predating session tracking) now
    # honestly show 0 visits and a 0% rate rather than a guess.
    channel_sessions = await analytics_repo.sessions_by_channel(
        store.id, period_start, now
    )
    campaign_sessions = await analytics_repo.sessions_by_campaign(
        store.id, period_start, now
    )
    landing_pages = await pv_repo.top_landing_pages(store.id, period_start, now)
    referrers = await pv_repo.top_referrers(store.id, period_start, now)

    channels = []
    for r in channel_rows:
        visits = channel_sessions.get(r["channel"], 0)
        channels.append(
            ChannelAttributionItem(
                channel=r["channel"],
                visits=visits,
                orders=r["orders"],
                revenue=r["revenue_cents"],
                conversion_rate=(
                    round(r["orders"] / visits * 100, 1) if visits > 0 else 0
                ),
            )
        )

    campaigns = []
    for r in campaign_rows:
        campaigns.append(
            CampaignItem(
                campaign=r["campaign"],
                visits=campaign_sessions.get(r["campaign"], 0),
                orders=r["orders"],
                revenue=r["revenue_cents"],
            )
        )

    return SuccessResponse(
        data=MarketingAttributionResponse(
            channels=channels,
            campaigns=campaigns,
            total_visits=total_visits,
            attributed_visits=attributed_visits,
            top_landing_pages=[LandingPageItem(**p) for p in landing_pages],
            top_referrers=[ReferrerItem(**r) for r in referrers],
        ),
        message="Marketing attribution retrieved successfully",
    )


# ── LTV by acquisition channel ──


class LtvChannelRow(BaseModel):
    """One row of LTV-by-channel rollup."""

    channel: str
    customer_count: int
    total_orders: int
    total_revenue_cents: int
    average_order_value_cents: int
    orders_per_customer: float
    ltv_cents: int  # total_revenue / customer_count


class LtvByChannelTotals(BaseModel):
    """Overall totals across all channels in the response window."""

    customer_count: int
    total_orders: int
    total_revenue_cents: int
    average_ltv_cents: int


class LtvByChannelResponse(BaseModel):
    """LTV by first-touch acquisition channel."""

    group_by: str
    channels: list[LtvChannelRow]
    totals: LtvByChannelTotals


_VALID_LTV_GROUP_BY = ("source", "medium", "campaign")


def _build_ltv_channel_row(
    channel: str,
    customer_count: int,
    total_orders: int,
    total_revenue_cents: int,
) -> LtvChannelRow:
    """Derive the per-channel metrics shown to the merchant.

    Integer-cent division (``//``) matches the AOV convention used
    elsewhere in this file (see ``/overview``); fractional cents would
    be misleading for an EGP-denominated dashboard. ``orders_per_customer``
    is float because it's an unbounded ratio, not a money quantity.
    Empty cohorts (no customers or no orders) collapse to zero rather
    than DivisionByZero.
    """
    aov = total_revenue_cents // total_orders if total_orders > 0 else 0
    opc = round(total_orders / customer_count, 2) if customer_count > 0 else 0.0
    ltv = total_revenue_cents // customer_count if customer_count > 0 else 0
    return LtvChannelRow(
        channel=channel,
        customer_count=customer_count,
        total_orders=total_orders,
        total_revenue_cents=total_revenue_cents,
        average_order_value_cents=aov,
        orders_per_customer=opc,
        ltv_cents=ltv,
    )


def _build_ltv_totals(rows: list[LtvChannelRow]) -> LtvByChannelTotals:
    """Aggregate the per-channel rows into a single totals row.

    ``average_ltv_cents`` is the **weighted** average across all
    customers in the response window (total revenue / total customers),
    not the mean of per-channel LTVs — the unweighted mean would skew
    toward channels with tiny cohorts.
    """
    total_customers = sum(r.customer_count for r in rows)
    total_orders = sum(r.total_orders for r in rows)
    total_revenue = sum(r.total_revenue_cents for r in rows)
    avg_ltv = total_revenue // total_customers if total_customers > 0 else 0
    return LtvByChannelTotals(
        customer_count=total_customers,
        total_orders=total_orders,
        total_revenue_cents=total_revenue,
        average_ltv_cents=avg_ltv,
    )


@router.get(
    "/ltv-by-channel",
    response_model=SuccessResponse[LtvByChannelResponse],
    summary="Get customer LTV by first-touch acquisition channel",
    operation_id="get_ltv_by_channel",
)
async def get_ltv_by_channel(
    store: Annotated[Store, Depends(verify_store_ownership)],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
    group_by: str = Query(
        "source",
        description=(
            "Acquisition dimension to bucket by: source, medium, or campaign. "
            "Sourced from customers.first_touch_attribution JSONB."
        ),
    ),
):
    """LTV by acquisition channel — customer cohorts joined to lifetime orders.

    The ``date_from``/``date_to`` window from the standard date-range
    dependency is applied to ``customers.first_touch_at`` (cohort
    selection), **not** to orders. The point of LTV-by-channel is to
    see how much revenue each acquisition cohort eventually produces
    over their entire history, so we don't crop the order side.

    Channels are read from ``customers.first_touch_attribution`` —
    customers without that field (no UTMs on first touch) fall into a
    ``"direct"`` bucket so they aren't dropped from the rollup.

    ``group_by`` accepts ``source`` (default), ``medium``, or
    ``campaign``. Invalid values 422 at the route layer before any
    DB work runs.
    """
    if group_by not in _VALID_LTV_GROUP_BY:
        raise HTTPException(
            status_code=422,
            detail=(
                f"group_by must be one of {list(_VALID_LTV_GROUP_BY)}; got {group_by!r}"
            ),
        )

    rows = await analytics_repo.ltv_by_channel(
        store.id,
        window.start,
        window.end,
        group_by=group_by,
    )

    channels = [
        _build_ltv_channel_row(
            channel=r["channel"],
            customer_count=r["customer_count"],
            total_orders=r["total_orders"],
            total_revenue_cents=r["total_revenue_cents"],
        )
        for r in rows
    ]

    return SuccessResponse(
        data=LtvByChannelResponse(
            group_by=group_by,
            channels=channels,
            totals=_build_ltv_totals(channels),
        ),
        message="LTV by channel retrieved successfully",
    )


# ── AI Insights ──


class InsightSignalResponse(BaseModel):
    type: str
    severity: str
    title_en: str
    title_ar: str
    body_en: str
    body_ar: str
    action_en: str | None = None
    action_ar: str | None = None
    metric: str | None = None
    current_value: float | None = None
    baseline_value: float | None = None
    deviation_pct: float | None = None


class InsightNarrativeResponse(BaseModel):
    summary: str | None = None
    top_actions: list[str] = []
    outlook: str | None = None


class InsightsResponse(BaseModel):
    signals: list[InsightSignalResponse]
    narrative: InsightNarrativeResponse | None = None
    generated_at: str


@router.get(
    "/insights",
    response_model=SuccessResponse[InsightsResponse],
    summary="Get AI-powered store insights",
    operation_id="get_insights",
)
async def get_insights(
    store: Annotated[Store, Depends(verify_store_ownership)],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    lang: str = Query("ar", description="Language: ar or en"),
):
    """Get AI-powered insights with anomaly detection and LLM narratives.

    Uses cached insights from store.settings when available (refreshed daily
    by Celery task). Falls back to live calculation if no cache exists.
    Same rollup-vs-orders fallback as ``/forecast``: brand-new stores
    have no rollup rows yet, so we aggregate from ``orders`` directly.
    """
    from src.application.services.ai_insights_service import generate_insights

    # Try cached insights first
    if store.settings:
        cached = store.settings.get("ai_insights")
        if cached and cached.get("generated_at"):
            # Use cache if generated today AND it represents real data.
            # An "insufficient data" cache is sticky-bad: the rollup
            # cron may have run on an empty store earlier today, then
            # the merchant placed orders — without this guard we'd
            # serve "0 days available" until tomorrow UTC.
            cached_signals = cached.get("signals", [])
            cached_metrics = cached.get("metrics_summary") or {}
            # The rule engine emits a single ``insufficient_data``
            # signal when it sees <7 days of rollup rows. That's the
            # reliable "cache was built on no data" marker — checking
            # for an empty signals list misses the case (the empty
            # state has 1 signal, not 0). Bypass either when we see
            # that signal type or when the cached metrics summary
            # explicitly says <7 days were analyzed.
            has_insufficient_signal = any(
                isinstance(s, dict) and s.get("type") == "insufficient_data"
                for s in cached_signals
            )
            cached_days = int(cached_metrics.get("days_analyzed") or 0)
            looks_empty = (
                not cached_signals or has_insufficient_signal or cached_days < 7
            )

            try:
                gen_time = datetime.fromisoformat(cached["generated_at"])
                if gen_time.date() == datetime.now(UTC).date() and not looks_empty:
                    signals = [InsightSignalResponse(**s) for s in cached_signals]
                    narrative = None
                    if cached.get("narrative"):
                        narrative = InsightNarrativeResponse(**cached["narrative"])
                    return SuccessResponse(
                        data=InsightsResponse(
                            signals=signals,
                            narrative=narrative,
                            generated_at=cached["generated_at"],
                        ),
                        message="Insights retrieved (cached)",
                    )
            except (ValueError, TypeError):
                pass

    # Live calculation
    today = date.today()
    date_from = today - timedelta(days=35)  # 5 weeks for baseline + current
    rollups = await rollup_repo.get_range(store.id, date_from, today)

    # Order-derived fallback: if the rollup is shorter than the order
    # history (typical on stores where the daily cron hasn't run yet),
    # synthesise rollup-shaped objects from a SQL aggregation. The
    # rule engine reads ``rollup_date / total_revenue_cents /
    # total_orders``, so a small adapter object is enough.
    from datetime import datetime as _dt

    period_from = _dt.combine(date_from, _dt.min.time()).replace(tzinfo=UTC)
    period_to = datetime.now(UTC)
    order_rows = await analytics_repo.daily_revenue_series(
        store.id,
        period_from,
        period_to,
        tz=resolve_store_timezone_name(store.settings),
    )
    if len(order_rows) > len(rollups):

        class _PseudoRollup:
            # Mirror every attribute the insights rule engine reads off
            # the real ``AnalyticsDailyRollupModel`` so it can iterate
            # the fallback rows interchangeably. Fields the SQL
            # aggregation doesn't supply default to zero / empty list —
            # the matching rule (COD-rejection, refunds, customer-mix,
            # traffic) simply won't fire, which is correct: we don't
            # have the data, so we can't trigger the signal.
            __slots__ = (
                "rollup_date",
                "total_revenue_cents",
                "total_orders",
                "paid_orders",
                "cancelled_orders",
                "avg_order_value_cents",
                "new_customers",
                "returning_customers",
                "total_page_views",
                "unique_visitors",
                "cod_orders",
                "cod_delivered",
                "cod_rejected",
                "refund_count",
                "refund_amount_cents",
                "top_products_json",
                "revenue_by_location_json",
                "traffic_sources_json",
            )

            def __init__(self, row: dict) -> None:
                self.rollup_date = row["rollup_date"]
                self.total_revenue_cents = row["total_revenue_cents"]
                self.total_orders = row["total_orders"]
                self.paid_orders = row.get("paid_orders", 0)
                self.cancelled_orders = row.get("cancelled_orders", 0)
                self.avg_order_value_cents = row.get("avg_order_value_cents", 0)
                self.new_customers = row.get("new_customers", 0)
                self.returning_customers = row.get("returning_customers", 0)
                self.total_page_views = row.get("total_page_views", 0)
                self.unique_visitors = row.get("unique_visitors", 0)
                self.cod_orders = row.get("cod_orders", 0)
                self.cod_delivered = row.get("cod_delivered", 0)
                self.cod_rejected = row.get("cod_rejected", 0)
                self.refund_count = row.get("refund_count", 0)
                self.refund_amount_cents = row.get("refund_amount_cents", 0)
                self.top_products_json = row.get("top_products_json", [])
                self.revenue_by_location_json = row.get("revenue_by_location_json", [])
                self.traffic_sources_json = row.get("traffic_sources_json", [])

        rollups = [_PseudoRollup(r) for r in order_rows]

    currency = store.default_currency.value if store.default_currency else "EGP"
    result = await generate_insights(rollups, store_currency=currency, lang=lang)

    # Cache result
    try:
        store_repo = StoreRepository(rollup_repo.session)
        current_settings = dict(store.settings) if store.settings else {}
        current_settings["ai_insights"] = result
        store.settings = current_settings
        await store_repo.update(store)
    except Exception:
        pass

    signals = [InsightSignalResponse(**s) for s in result.get("signals", [])]
    narrative = None
    if result.get("narrative"):
        narrative = InsightNarrativeResponse(**result["narrative"])

    return SuccessResponse(
        data=InsightsResponse(
            signals=signals,
            narrative=narrative,
            generated_at=result.get("generated_at", ""),
        ),
        message="Insights generated",
    )


# ── Sales Forecast ──


class ForecastPointResponse(BaseModel):
    date: str
    predicted: int  # cents
    lower: int  # cents
    upper: int  # cents


class HistoricalPointResponse(BaseModel):
    date: str
    revenue: int  # cents
    orders: int


class ForecastMetadataResponse(BaseModel):
    status: str
    days_available: int | None = None
    days_required: int | None = None
    horizon_days: int | None = None
    method: str | None = None
    avg_daily_revenue_7d: int | None = None
    forecast_total: int | None = None
    forecast_daily_avg: int | None = None
    trend: str | None = None
    message_en: str | None = None
    message_ar: str | None = None


class ForecastResponse(BaseModel):
    historical: list[HistoricalPointResponse]
    forecast: list[ForecastPointResponse]
    metadata: ForecastMetadataResponse


@router.get(
    "/forecast",
    response_model=SuccessResponse[ForecastResponse],
    summary="Get sales forecast",
    operation_id="get_forecast",
)
async def get_forecast(
    store: Annotated[Store, Depends(verify_store_ownership)],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    analytics_repo: Annotated[AnalyticsRepository, Depends(get_analytics_repository)],
    horizon: int = Query(30, ge=7, le=90, description="Forecast horizon in days"),
):
    """Get sales revenue forecast using Holt-Winters exponential smoothing.

    Preferred source: the nightly ``analytics_daily_rollup`` table.
    Fallback: aggregate ``orders`` directly when the rollup hasn't
    caught up to this store yet (brand-new merchants, or the daily
    cron hasn't fired since signup). The forecast service consumes a
    sequence of rollup-shaped objects, so we just wrap the raw daily
    rows in a tiny adapter when we use the fallback path.
    """
    from src.application.services.forecast_service import generate_forecast

    today = date.today()
    date_from = today - timedelta(days=365)  # Use up to 1 year of data
    rollups = await rollup_repo.get_range(store.id, date_from, today)

    # The forecast service requires ≥14 days of data. If the rollup
    # table is short of that, try aggregating orders directly — most
    # of the time this is the difference between "0 days" (rollup
    # hasn't run yet) and "you have enough" for a real merchant.
    if len(rollups) < 14:
        from datetime import datetime as _dt

        period_from = _dt.combine(date_from, _dt.min.time()).replace(tzinfo=UTC)
        period_to = datetime.now(UTC)
        order_rows = await analytics_repo.daily_revenue_series(
            store.id,
            period_from,
            period_to,
            tz=resolve_store_timezone_name(store.settings),
        )
        if len(order_rows) > len(rollups):

            class _PseudoRollup:
                __slots__ = ("rollup_date", "total_revenue_cents", "total_orders")

                def __init__(self, row: dict) -> None:
                    self.rollup_date = row["rollup_date"]
                    self.total_revenue_cents = row["total_revenue_cents"]
                    self.total_orders = row["total_orders"]

            rollups = [_PseudoRollup(r) for r in order_rows]

    result = generate_forecast(rollups, horizon=horizon)

    historical = [HistoricalPointResponse(**h) for h in result.get("historical", [])]
    forecast = [ForecastPointResponse(**f) for f in result.get("forecast", [])]
    metadata = ForecastMetadataResponse(**result.get("metadata", {"status": "error"}))

    return SuccessResponse(
        data=ForecastResponse(
            historical=historical,
            forecast=forecast,
            metadata=metadata,
        ),
        message="Forecast generated",
    )


# ── Customer Journey / Sessions ──


def _parse_device_type(user_agent: str | None) -> str:
    """Parse device type from user agent string."""
    if not user_agent:
        return "unknown"
    ua = user_agent.lower()
    if any(k in ua for k in ("mobile", "android", "iphone", "ipad")):
        if "ipad" in ua or "tablet" in ua:
            return "tablet"
        return "mobile"
    return "desktop"


class SessionSummaryItem(BaseModel):
    session_fingerprint: str
    page_count: int
    duration_seconds: int
    started_at: str
    device_type: str
    referrer: str | None
    funnel_reached: str | None  # deepest funnel step
    has_order: bool


class SessionsOverview(BaseModel):
    total_sessions: int
    avg_duration_seconds: int
    bounce_rate: float  # % sessions with 1 page
    sessions_with_order_pct: float


class SessionsResponse(BaseModel):
    overview: SessionsOverview
    sessions: list[SessionSummaryItem]


@router.get(
    "/sessions",
    response_model=SuccessResponse[SessionsResponse],
    summary="Get customer sessions list",
    operation_id="get_sessions",
)
async def get_sessions(
    store: Annotated[Store, Depends(verify_store_ownership)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
    has_order: bool = Query(False, description="Filter to sessions with orders"),
    min_pages: int = Query(1, ge=1, description="Minimum page count"),
    device: str = Query("", description="Filter by device: mobile, desktop, tablet"),
):
    """Get session list with duration, pages, funnel reached, and device type.

    Span is clamped by the shared date-range dependency. The legacy
    cap was 30 days; with ``granularity=hour`` (the default in the
    picker for ≤48 h spans) the cap is 7 days.
    """
    period_start = window.start
    now = window.end

    raw_sessions = await pv_repo.get_sessions_summary(
        store.id, period_start, now, limit=500
    )

    if not raw_sessions:
        return SuccessResponse(
            data=SessionsResponse(
                overview=SessionsOverview(
                    total_sessions=0,
                    avg_duration_seconds=0,
                    bounce_rate=0,
                    sessions_with_order_pct=0,
                ),
                sessions=[],
            ),
            message="Sessions retrieved",
        )

    # Single query: ``{fingerprint: {step1, step2, ...}}``. Replaces a
    # loop that fired ``get_sessions_with_step`` once per funnel step.
    steps_by_fp = await funnel_repo.get_steps_per_session(store.id, period_start, now)

    funnel_steps_order = [
        "page_view",
        "product_view",
        "add_to_cart",
        "checkout_started",
        "order_completed",
        "order_delivered",
    ]

    sessions: list[SessionSummaryItem] = []
    durations: list[int] = []
    bounces = 0

    for s in raw_sessions:
        fp = s["session_fingerprint"]
        page_count = s["page_count"]
        started = s["started_at"]
        ended = s["ended_at"]
        duration = int((ended - started).total_seconds()) if started and ended else 0
        device_type = _parse_device_type(s.get("user_agent"))
        fp_steps = steps_by_fp.get(fp, set())
        # A session "has an order" only when a purchase actually
        # completed — the previous check used checkout_started, which
        # counted every abandoned checkout as a conversion and
        # overstated sessions_with_order_pct.
        in_orders = "order_completed" in fp_steps

        # Deepest funnel step (last one of the canonical order present
        # in the session's step set).
        deepest = "page_view"
        for step in funnel_steps_order[1:]:
            if step in fp_steps:
                deepest = step

        # Apply filters
        if has_order and not in_orders:
            continue
        if page_count < min_pages:
            continue
        if device and device_type != device:
            continue

        if page_count <= 1:
            bounces += 1

        durations.append(duration)
        sessions.append(
            SessionSummaryItem(
                session_fingerprint=fp,
                page_count=page_count,
                duration_seconds=duration,
                started_at=started.isoformat(),
                device_type=device_type,
                referrer=s.get("referrer"),
                funnel_reached=deepest,
                has_order=in_orders,
            )
        )

    total = len(sessions)
    avg_dur = sum(durations) // total if total > 0 else 0
    bounce_rate = round(bounces / total * 100, 1) if total > 0 else 0
    order_pct = (
        round(sum(1 for s in sessions if s.has_order) / total * 100, 1)
        if total > 0
        else 0
    )

    return SuccessResponse(
        data=SessionsResponse(
            overview=SessionsOverview(
                total_sessions=total,
                avg_duration_seconds=avg_dur,
                bounce_rate=bounce_rate,
                sessions_with_order_pct=order_pct,
            ),
            sessions=sessions[:100],  # Cap at 100 for response size
        ),
        message="Sessions retrieved",
    )


# ── Session Detail (Timeline) ──


class TimelineEvent(BaseModel):
    type: str  # "page_view" or funnel step name
    path: str | None = None
    step_data: dict | None = None
    timestamp: str


class SessionDetailResponse(BaseModel):
    session_fingerprint: str
    device_type: str
    referrer: str | None
    page_count: int
    duration_seconds: int
    timeline: list[TimelineEvent]


@router.get(
    "/sessions/{fingerprint}",
    response_model=SuccessResponse[SessionDetailResponse],
    summary="Get session detail timeline",
    operation_id="get_session_detail",
)
async def get_session_detail(
    store: Annotated[Store, Depends(verify_store_ownership)],
    fingerprint: str,
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
):
    """Get full timeline for a specific session."""
    pages = await pv_repo.get_session_pages(store.id, fingerprint)
    events = await funnel_repo.get_session_events(store.id, fingerprint)

    # Merge into unified timeline
    timeline: list[dict] = []

    for p in pages:
        timeline.append({
            "type": "page_view",
            "path": p["path"],
            "step_data": None,
            "timestamp": p["created_at"],
        })

    for e in events:
        if e["step"] == "page_view":
            continue  # Already covered by page_views
        timeline.append({
            "type": e["step"],
            "path": e["step_data"].get("path") if e["step_data"] else None,
            "step_data": e["step_data"],
            "timestamp": e["created_at"],
        })

    # Sort by timestamp
    timeline.sort(key=lambda x: x["timestamp"])

    # Compute session metadata
    device_type = _parse_device_type(pages[0]["user_agent"] if pages else None)
    referrer = pages[0]["referrer"] if pages else None
    page_count = len(pages)

    if timeline:
        first = timeline[0]["timestamp"]
        last = timeline[-1]["timestamp"]
        duration = int((last - first).total_seconds())
    else:
        duration = 0

    formatted = [
        TimelineEvent(
            type=t["type"],
            path=t["path"],
            step_data=t["step_data"],
            timestamp=t["timestamp"].isoformat()
            if hasattr(t["timestamp"], "isoformat")
            else str(t["timestamp"]),
        )
        for t in timeline
    ]

    return SuccessResponse(
        data=SessionDetailResponse(
            session_fingerprint=fingerprint,
            device_type=device_type,
            referrer=referrer,
            page_count=page_count,
            duration_seconds=duration,
            timeline=formatted,
        ),
        message="Session detail retrieved",
    )


# ── Multi-touch attribution ──────────────────────────────────────────


class MultiTouchChannelRow(BaseModel):
    channel: str
    credit_cents: int
    credit_pct: float


class MultiTouchCampaignRow(BaseModel):
    campaign_id: str
    campaign_name: str
    credit_cents: int
    credit_pct: float


class MultiTouchAttributionResponse(BaseModel):
    model: str
    total_orders: int
    total_revenue_cents: int
    by_channel: list[MultiTouchChannelRow]
    by_campaign: list[MultiTouchCampaignRow]


_VALID_ATTRIBUTION_MODELS = (
    "last_touch",
    "first_touch",
    "linear",
    "time_decay",
    "position_based",
)


@router.get(
    "/multi-touch",
    response_model=SuccessResponse[MultiTouchAttributionResponse],
    summary="Multi-touch attribution per channel + campaign",
    operation_id="get_multi_touch_attribution",
)
async def get_multi_touch_attribution(
    store: Annotated[Store, Depends(verify_store_ownership)],
    window: Annotated[DateRangeWindow, Depends(get_date_range_window)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    model: str = Query(
        "linear",
        description=(
            "Attribution model: last_touch, first_touch, linear, "
            "time_decay, position_based"
        ),
    ),
):
    """Distribute order revenue across acquisition touches per the selected model.

    Reads ``orders`` × ``customer_touches`` in the date window and
    runs one of five attribution models:

    * ``last_touch`` — 100% to the last touch (legacy)
    * ``first_touch`` — 100% to the first touch
    * ``linear`` — equal split across all touches
    * ``time_decay`` — exponential decay, 7-day half-life (Google
      Ads default)
    * ``position_based`` — Shopify-style U-shape, 40/20/40

    Returns per-channel credit AND per-campaign credit (only for
    touches that resolved to a known marketing_campaigns row). 400
    when the window contains too many orders to attribute on demand
    (5000-order cap — narrow the date range and retry).
    """
    if model not in _VALID_ATTRIBUTION_MODELS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"model must be one of {list(_VALID_ATTRIBUTION_MODELS)}; got {model!r}"
            ),
        )

    from src.application.services import multi_touch_attribution

    try:
        result = await multi_touch_attribution.compute_multi_touch_attribution(
            session=order_repo.session,
            store_id=store.id,
            date_from=window.start,
            date_to=window.end,
            model=model,  # type: ignore[arg-type]
        )
    except multi_touch_attribution.AttributionWindowTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SuccessResponse(
        data=MultiTouchAttributionResponse(
            model=result["model"],
            total_orders=result["total_orders"],
            total_revenue_cents=result["total_revenue_cents"],
            by_channel=[MultiTouchChannelRow(**row) for row in result["by_channel"]],
            by_campaign=[MultiTouchCampaignRow(**row) for row in result["by_campaign"]],
        ),
        message="Multi-touch attribution computed",
    )
