"""Analytics routes nested under stores.

URL: /stores/{store_id}/analytics
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.dependencies import (
    get_customer_repository,
    get_order_repository,
    get_product_repository,
    get_shipment_repository,
    verify_store_ownership,
)
from src.api.dependencies.repositories import (
    get_analytics_rollup_repository,
    get_funnel_event_repository,
    get_page_view_repository,
)
from src.api.responses import SuccessResponse
from src.application.services.health_score_service import calculate_store_health_score
from src.core.entities.order import OrderStatus, PaymentStatus
from src.core.entities.store import Store
from src.infrastructure.repositories import (
    AnalyticsRollupRepository,
    CustomerRepository,
    OrderRepository,
    StoreRepository,
)
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)
from src.infrastructure.repositories.page_view_repository import PageViewRepository
from src.infrastructure.repositories.product_repository import ProductRepository
from src.infrastructure.repositories.shipment_repository import ShipmentRepository

router = APIRouter(prefix="/{store_id}/analytics")


class SalesOverviewResponse(BaseModel):
    """Sales overview statistics."""

    total_sales: int  # In cents
    total_orders: int
    avg_order_value: int  # In cents
    sales_change_percent: float
    orders_change_percent: float
    currency: str


class SalesDataPointResponse(BaseModel):
    """Sales data point for charts."""

    date: str
    sales: int
    orders: int


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
    days: int = Query(30, ge=1, le=365, description="Number of days"),
):
    """Get sales overview for the store (uses pre-aggregated rollup data)."""
    today = date.today()
    period_start = today - timedelta(days=days)
    previous_period_start = period_start - timedelta(days=days)

    # Try rollup table first
    current = await rollup_repo.get_aggregated(store.id, period_start, today)
    previous = await rollup_repo.get_aggregated(
        store.id, previous_period_start, period_start
    )

    current_revenue = current["total_revenue_cents"]
    current_orders = current["total_orders"]
    previous_revenue = previous["total_revenue_cents"]
    previous_orders = previous["total_orders"]

    # If rollup is empty, fall back to raw query (first run / no rollup yet)
    if current_revenue == 0 and current_orders == 0:
        now = datetime.now(UTC)
        ps = now - timedelta(days=days)
        pps = ps - timedelta(days=days)
        current_revenue = await order_repo.get_revenue_by_date_range(store.id, ps, now)
        current_orders = await order_repo.count_by_store(
            store.id, date_from=ps, date_to=now
        )
        previous_revenue = await order_repo.get_revenue_by_date_range(store.id, pps, ps)
        previous_orders = await order_repo.count_by_store(
            store.id, date_from=pps, date_to=ps
        )

    # Calculate changes
    if previous_revenue > 0:
        sales_change = ((current_revenue - previous_revenue) / previous_revenue) * 100
    else:
        sales_change = 100.0 if current_revenue > 0 else 0.0

    if previous_orders > 0:
        orders_change = ((current_orders - previous_orders) / previous_orders) * 100
    else:
        orders_change = 100.0 if current_orders > 0 else 0.0

    avg_order_value = current_revenue // current_orders if current_orders > 0 else 0

    return SuccessResponse(
        data=SalesOverviewResponse(
            total_sales=current_revenue,
            total_orders=current_orders,
            avg_order_value=avg_order_value,
            sales_change_percent=round(sales_change, 1),
            orders_change_percent=round(orders_change, 1),
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
    days: int = Query(30, ge=1, le=365, description="Number of days"),
):
    """Get sales data for chart visualization (single query via rollup)."""
    today = date.today()
    date_from = today - timedelta(days=days)

    # Single query on rollup table instead of N individual queries
    rollups = await rollup_repo.get_range(store.id, date_from, today)

    if rollups:
        # Build a date->rollup lookup for gap filling
        rollup_map = {r.rollup_date: r for r in rollups}
        data_points = []
        for i in range(days):
            d = date_from + timedelta(days=i)
            r = rollup_map.get(d)
            data_points.append(
                SalesDataPointResponse(
                    date=d.strftime("%b %d"),
                    sales=r.total_revenue_cents if r else 0,
                    orders=r.total_orders if r else 0,
                )
            )
    else:
        # Fallback when the rollup table is empty (first run of the day,
        # brand-new install). Single GROUP-BY-day query instead of the
        # previous N+1 (two queries × 30 days). Gap-fills missing days
        # with zeros so the chart shape stays consistent.
        start_dt = datetime.combine(date_from, datetime.min.time(), tzinfo=UTC)
        end_dt = datetime.combine(today, datetime.max.time(), tzinfo=UTC)
        rows = await order_repo.get_daily_aggregates(store.id, start_dt, end_dt)
        by_day = {row[0]: row for row in rows}
        data_points = []
        for i in range(days):
            d = date_from + timedelta(days=i)
            row = by_day.get(d)
            data_points.append(
                SalesDataPointResponse(
                    date=d.strftime("%b %d"),
                    sales=row[1] if row else 0,
                    orders=row[2] if row else 0,
                )
            )

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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
):
    """Get top selling products (merges pre-aggregated rollup data)."""
    today = date.today()
    date_from = today - timedelta(days=days)

    rollups = await rollup_repo.get_range(store.id, date_from, today)

    if rollups:
        # Merge top_products_json across rollup days
        merged: dict[str, dict] = {}
        for r in rollups:
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

        sorted_products = sorted(
            merged.values(), key=lambda x: x["revenue"], reverse=True
        )[:limit]
        total_revenue = sum(p["revenue"] for p in merged.values())
    else:
        # Fallback to raw query
        now = datetime.now(UTC)
        period_start = now - timedelta(days=days)
        orders = await order_repo.get_by_date_range(
            store.id, period_start, now, limit=1000
        )
        product_sales: dict[str, dict] = {}
        total_revenue = 0
        for order in orders:
            if order.payment_status not in [
                PaymentStatus.PAID,
                PaymentStatus.PARTIALLY_REFUNDED,
            ]:
                continue
            for item in order.line_items:
                pid = str(item.product_id)
                if pid not in product_sales:
                    product_sales[pid] = {
                        "id": pid,
                        "name": item.product_name,
                        "sku": item.sku,
                        "quantity": 0,
                        "revenue": 0,
                    }
                product_sales[pid]["quantity"] += item.quantity
                product_sales[pid]["revenue"] += item.total_price
                total_revenue += item.total_price
        sorted_products = sorted(
            product_sales.values(), key=lambda x: x["revenue"], reverse=True
        )[:limit]

    result = []
    for p in sorted_products:
        percentage = (p["revenue"] / total_revenue * 100) if total_revenue > 0 else 0
        result.append(
            AnalyticsTopProductResponse(
                id=str(p["id"]),
                name=p["name"],
                sku=p["sku"],
                quantity_sold=p["quantity"],
                revenue=p["revenue"],
                percentage=round(percentage, 1),
            )
        )

    return SuccessResponse(
        data=result,
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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get sales breakdown by location/governorate."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=1000)

    # Aggregate by location
    location_sales: dict[str, dict] = {}
    total_sales = 0

    for order in orders:
        if order.payment_status not in [
            PaymentStatus.PAID,
            PaymentStatus.PARTIALLY_REFUNDED,
        ]:
            continue

        # Get location from shipping address
        location = "Unknown"
        if order.shipping_address:
            location = (
                order.shipping_address.city or order.shipping_address.state or "Unknown"
            )

        if location not in location_sales:
            location_sales[location] = {"sales": 0, "orders": 0}

        location_sales[location]["sales"] += order.total
        location_sales[location]["orders"] += 1
        total_sales += order.total

    # Sort by sales and format
    sorted_locations = sorted(
        location_sales.items(),
        key=lambda x: x[1]["sales"],
        reverse=True,
    )

    result = []
    for location, data in sorted_locations:
        percentage = (data["sales"] / total_sales * 100) if total_sales > 0 else 0
        result.append(
            SalesByLocationResponse(
                location=location,
                sales=data["sales"],
                orders=data["orders"],
                percentage=round(percentage, 1),
            )
        )

    return SuccessResponse(
        data=result,
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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get customer analytics for the store."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    total_customers = await customer_repo.count_by_store(store.id)

    # Get orders to calculate metrics
    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=1000)

    # Count unique customers and their order frequency
    customer_orders: dict[UUID, int] = {}
    total_revenue = 0

    for order in orders:
        if order.customer_id:
            customer_orders[order.customer_id] = (
                customer_orders.get(order.customer_id, 0) + 1
            )
        total_revenue += order.total

    new_customers = sum(1 for count in customer_orders.values() if count == 1)
    returning_customers = sum(1 for count in customer_orders.values() if count > 1)

    avg_customer_value = total_revenue // len(customer_orders) if customer_orders else 0

    return SuccessResponse(
        data=CustomerAnalyticsResponse(
            total_customers=total_customers,
            new_customers=new_customers,
            returning_customers=returning_customers,
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
    days: int = Query(30, ge=1, le=365),
):
    """Get conversion statistics for the store."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=1000)
    total_orders = len(orders)

    total_visitors = await pv_repo.count_unique_visitors(store.id, period_start, now)
    conversion_rate = (total_orders / total_visitors * 100) if total_visitors > 0 else 0

    # Cart abandonment - would need cart tracking
    cart_abandonment_rate = 70.0

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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get order attribution by UTM source."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=5000)

    source_data: dict[str, dict] = {}
    total_revenue = 0

    for order in orders:
        source = order.utm_source or "direct"
        if source not in source_data:
            source_data[source] = {"orders": 0, "revenue": 0}
        source_data[source]["orders"] += 1
        source_data[source]["revenue"] += order.total
        total_revenue += order.total

    sorted_sources = sorted(
        source_data.items(),
        key=lambda x: x[1]["revenue"],
        reverse=True,
    )

    result = []
    for source, data in sorted_sources:
        percentage = (data["revenue"] / total_revenue * 100) if total_revenue > 0 else 0
        result.append(
            TrafficSourceResponse(
                source=source,
                orders=data["orders"],
                revenue=data["revenue"],
                percentage=round(percentage, 1),
            )
        )

    return SuccessResponse(
        data=result,
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
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get COD rejection rate and breakdown by location."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    stats = await shipment_repo.get_cod_stats_by_store(store.id, period_start, now)
    locations = await shipment_repo.get_cod_rejection_by_location(
        store.id, period_start, now
    )

    total = stats["total"]
    rejected = stats["failed"] + stats["returned"]
    rejection_rate = round((rejected / total) * 100, 1) if total > 0 else 0.0

    return SuccessResponse(
        data=CodRejectionStatsResponse(
            total_cod_shipments=total,
            delivered_count=stats["delivered"],
            rejected_count=rejected,
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
    score: int
    grade: str
    metrics: HealthScoreMetrics
    sub_scores: dict[str, int]
    recommendations: list[str]
    orders_analyzed: int
    shipments_analyzed: int
    calculated_at: str | None


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
    """
    # Try cached score first (from daily Celery task)
    if not live and store.settings:
        cached = store.settings.get("health_score")
        if cached:
            return SuccessResponse(
                data=HealthScoreResponse(**cached),
                message="Health score retrieved (cached)",
            )

    # Live calculation (first visit or explicit live=true)

    score_data = await calculate_store_health_score(
        session=order_repo.session,
        store_id=store.id,
        days=30,
        lang=lang if lang in ("ar", "en") else "ar",
    )

    # Cache the result in store.settings for next time
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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get orders breakdown by status, payment method, time distribution."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=5000)
    total = len(orders)

    # By status
    status_counts: dict[str, int] = {}
    for o in orders:
        s = o.status.value if isinstance(o.status, OrderStatus) else str(o.status)
        status_counts[s] = status_counts.get(s, 0) + 1
    by_status = [
        OrdersByStatusItem(
            status=s,
            count=c,
            percentage=round(c / total * 100, 1) if total > 0 else 0,
        )
        for s, c in sorted(status_counts.items(), key=lambda x: x[1], reverse=True)
    ]

    # By payment method
    method_data: dict[str, dict] = {}
    for o in orders:
        m = o.payment_method or "unknown"
        if m not in method_data:
            method_data[m] = {"count": 0, "revenue": 0}
        method_data[m]["count"] += 1
        method_data[m]["revenue"] += o.total
    by_payment_method = [
        OrdersByPaymentMethodItem(method=m, count=d["count"], revenue=d["revenue"])
        for m, d in sorted(
            method_data.items(), key=lambda x: x[1]["revenue"], reverse=True
        )
    ]

    # Fulfillment time (created_at → shipped_at for shipped/delivered orders)
    fulfillment_hours: list[float] = []
    for o in orders:
        if o.shipped_at and o.created_at:
            delta = o.shipped_at - o.created_at
            fulfillment_hours.append(delta.total_seconds() / 3600)

    if fulfillment_hours:
        fulfillment_hours.sort()
        avg_h = sum(fulfillment_hours) / len(fulfillment_hours)
        p50_idx = int(len(fulfillment_hours) * 0.5)
        p95_idx = min(int(len(fulfillment_hours) * 0.95), len(fulfillment_hours) - 1)
        fulfillment_time = FulfillmentTimeStats(
            avg_hours=round(avg_h, 1),
            p50_hours=round(fulfillment_hours[p50_idx], 1),
            p95_hours=round(fulfillment_hours[p95_idx], 1),
        )
    else:
        fulfillment_time = FulfillmentTimeStats(avg_hours=0, p50_hours=0, p95_hours=0)

    # By day of week
    day_names = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    day_data: dict[int, dict] = {i: {"orders": 0, "revenue": 0} for i in range(7)}
    for o in orders:
        wd = o.created_at.weekday()
        day_data[wd]["orders"] += 1
        day_data[wd]["revenue"] += o.total
    by_day_of_week = [
        OrdersByDayOfWeekItem(
            day=day_names[i],
            orders=day_data[i]["orders"],
            revenue=day_data[i]["revenue"],
        )
        for i in range(7)
    ]

    # By hour of day
    hour_data: dict[int, int] = dict.fromkeys(range(24), 0)
    for o in orders:
        hour_data[o.created_at.hour] += 1
    by_hour_of_day = [
        OrdersByHourItem(hour=h, orders=c) for h, c in sorted(hour_data.items())
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


class RevenueBreakdownResponse(BaseModel):
    gross_revenue: int  # cents
    discounts: int  # cents
    shipping_collected: int  # cents
    refunds: int  # cents
    net_revenue: int  # cents
    coupon_usage: list[CouponUsageItem]


@router.get(
    "/revenue-breakdown",
    response_model=SuccessResponse[RevenueBreakdownResponse],
    summary="Get revenue breakdown analytics",
    operation_id="get_revenue_breakdown",
)
async def get_revenue_breakdown(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    rollup_repo: Annotated[
        AnalyticsRollupRepository, Depends(get_analytics_rollup_repository)
    ],
    days: int = Query(30, ge=1, le=365),
):
    """Get revenue breakdown: gross, discounts, shipping, refunds, net."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)
    today = date.today()
    date_from = today - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=5000)

    gross_revenue = 0
    discounts = 0
    shipping_collected = 0
    coupon_data: dict[str, dict] = {}

    for o in orders:
        if o.payment_status not in [
            PaymentStatus.PAID,
            PaymentStatus.PARTIALLY_REFUNDED,
        ]:
            continue
        gross_revenue += o.subtotal
        discounts += o.discount_amount
        shipping_collected += o.shipping_cost

        if o.coupon_code:
            code = o.coupon_code
            if code not in coupon_data:
                coupon_data[code] = {"uses": 0, "revenue_impact": 0}
            coupon_data[code]["uses"] += 1
            coupon_data[code]["revenue_impact"] += o.discount_amount

    # Refunds from rollup (already aggregated)
    agg = await rollup_repo.get_aggregated(store.id, date_from, today)
    refunds = agg["refund_amount_cents"]

    net_revenue = gross_revenue - refunds

    coupon_usage = [
        CouponUsageItem(code=code, uses=d["uses"], revenue_impact=d["revenue_impact"])
        for code, d in sorted(
            coupon_data.items(), key=lambda x: x[1]["uses"], reverse=True
        )
    ]

    return SuccessResponse(
        data=RevenueBreakdownResponse(
            gross_revenue=gross_revenue,
            discounts=discounts,
            shipping_collected=shipping_collected,
            refunds=refunds,
            net_revenue=net_revenue,
            coupon_usage=coupon_usage,
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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(90, ge=30, le=365),
):
    """Get customer segmentation using RFM analysis, cohort retention, and CLV."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(
        store.id, period_start, now, limit=10000
    )

    # Build per-customer stats
    customer_stats: dict[UUID, dict] = {}
    for o in orders:
        if not o.customer_id or o.status == OrderStatus.CANCELLED:
            continue
        cid = o.customer_id
        if cid not in customer_stats:
            customer_stats[cid] = {
                "orders": 0,
                "total_spent": 0,
                "last_order": o.created_at,
                "first_order": o.created_at,
            }
        cs = customer_stats[cid]
        cs["orders"] += 1
        cs["total_spent"] += o.total
        if o.created_at > cs["last_order"]:
            cs["last_order"] = o.created_at
        if o.created_at < cs["first_order"]:
            cs["first_order"] = o.created_at

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

    # Cohort retention (month-over-month)
    # Group customers by first order month
    cohort_customers: dict[str, set[UUID]] = {}
    customer_order_months: dict[UUID, set[str]] = {}

    for o in orders:
        if not o.customer_id or o.status == OrderStatus.CANCELLED:
            continue
        cid = o.customer_id
        month_key = o.created_at.strftime("%Y-%m")
        if cid not in customer_order_months:
            customer_order_months[cid] = set()
        customer_order_months[cid].add(month_key)

    for cid, cs in customer_stats.items():
        cohort_key = cs["first_order"].strftime("%Y-%m")
        if cohort_key not in cohort_customers:
            cohort_customers[cohort_key] = set()
        cohort_customers[cohort_key].add(cid)

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


@router.get(
    "/product-performance",
    response_model=SuccessResponse[ProductPerformanceResponse],
    summary="Get product performance analytics",
    operation_id="get_product_performance",
)
async def get_product_performance(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    days: int = Query(30, ge=1, le=365),
    sort_by: str = Query("revenue", description="Sort by: revenue, quantity, name"),
):
    """Get product-level performance, category breakdown, and inventory health."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=5000)

    # Aggregate product-level sales from line_items
    product_sales: dict[str, dict] = {}
    # For 7-day trend: bucket by (product_id, date)
    seven_days_ago = now - timedelta(days=7)
    product_daily: dict[str, dict[str, int]] = {}

    for o in orders:
        if o.payment_status not in [
            PaymentStatus.PAID,
            PaymentStatus.PARTIALLY_REFUNDED,
        ]:
            continue
        order_date = o.created_at.strftime("%Y-%m-%d")
        for item in o.line_items:
            pid = str(item.product_id)
            if pid not in product_sales:
                product_sales[pid] = {
                    "name": item.product_name,
                    "sku": item.sku,
                    "revenue": 0,
                    "quantity": 0,
                }
            product_sales[pid]["revenue"] += item.total_price
            product_sales[pid]["quantity"] += item.quantity

            # Track daily for trend (last 7 days only)
            if o.created_at >= seven_days_ago:
                if pid not in product_daily:
                    product_daily[pid] = {}
                product_daily[pid][order_date] = (
                    product_daily[pid].get(order_date, 0) + item.total_price
                )

    # Get current products for stock info and category mapping
    products = await product_repo.get_by_store(store.id, skip=0, limit=5000)
    product_map = {str(p.id): p for p in products}

    # Build 7-day trend arrays
    trend_dates = [(now - timedelta(days=6 - i)).strftime("%Y-%m-%d") for i in range(7)]

    # Sort products
    sort_key = {
        "revenue": lambda x: x[1]["revenue"],
        "quantity": lambda x: x[1]["quantity"],
        "name": lambda x: x[1]["name"].lower(),
    }.get(sort_by, lambda x: x[1]["revenue"])
    reverse = sort_by != "name"

    sorted_products = sorted(product_sales.items(), key=sort_key, reverse=reverse)

    product_items = []
    for pid, data in sorted_products[:50]:  # Top 50
        p = product_map.get(pid)
        stock = p.quantity if p else 0
        trend = [product_daily.get(pid, {}).get(d, 0) for d in trend_dates]

        cost_cents: int | None = None
        profit_cents: int | None = None
        margin_pct: float | None = None
        if p is not None and p.cost_price is not None:
            cost_cents = p.cost_price.cents
            profit_cents = data["revenue"] - (cost_cents * data["quantity"])
            if data["revenue"] > 0:
                margin_pct = round(profit_cents / data["revenue"] * 100, 1)

        product_items.append(
            ProductPerformanceItem(
                id=pid,
                name=data["name"],
                sku=data["sku"],
                revenue=data["revenue"],
                quantity_sold=data["quantity"],
                current_stock=stock,
                revenue_trend=trend,
                cost_price=cost_cents,
                profit=profit_cents,
                margin_percent=margin_pct,
            )
        )

    # Category-level aggregation
    category_data: dict[str | None, dict] = {}
    for pid, data in product_sales.items():
        p = product_map.get(pid)
        cat_id = str(p.category_id) if p and p.category_id else None
        cat_name = "Uncategorized"
        if p and p.category_id and hasattr(p, "category") and p.category:
            cat_name = p.category.name
        elif cat_id:
            cat_name = f"Category {cat_id[:8]}"

        if cat_id not in category_data:
            category_data[cat_id] = {
                "name": cat_name,
                "revenue": 0,
                "quantity": 0,
                "products": set(),
            }
        category_data[cat_id]["revenue"] += data["revenue"]
        category_data[cat_id]["quantity"] += data["quantity"]
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

    # Inventory health
    sold_product_ids = set(product_sales.keys())
    in_stock = 0
    low_stock = 0
    out_of_stock = 0
    dead_stock = 0

    for p in products:
        pid = str(p.id)
        if p.quantity <= 0:
            out_of_stock += 1
        elif p.quantity <= p.low_stock_threshold:
            low_stock += 1
        else:
            in_stock += 1
        # Dead stock: has stock but zero sales in period
        if p.quantity > 0 and pid not in sold_product_ids:
            dead_stock += 1

    return SuccessResponse(
        data=ProductPerformanceResponse(
            products=product_items,
            categories=categories,
            inventory=InventoryHealthResponse(
                in_stock=in_stock,
                low_stock=low_stock,
                out_of_stock=out_of_stock,
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
    days: int = Query(30, ge=1, le=365),
):
    """Get conversion funnel step counts with drop-off percentages."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

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


class MarketingAttributionResponse(BaseModel):
    channels: list[ChannelAttributionItem]
    campaigns: list[CampaignItem]
    total_visits: int
    attributed_visits: int  # visits with UTM data


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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get marketing channel attribution from order UTM data and page views."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    # Get total visits
    total_visits = await pv_repo.count_unique_visitors(store.id, period_start, now)

    # Get orders with UTM data for attribution
    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=5000)

    channel_data: dict[str, dict] = {}
    campaign_data: dict[str, dict] = {}
    attributed_visits = 0

    for o in orders:
        channel = _classify_channel(o.utm_source, o.utm_medium)

        if o.utm_source and o.utm_source != "direct":
            attributed_visits += 1

        if channel not in channel_data:
            channel_data[channel] = {"visits": 0, "orders": 0, "revenue": 0}
        channel_data[channel]["orders"] += 1
        channel_data[channel]["revenue"] += o.total

        # Campaign tracking
        if o.utm_campaign:
            camp = o.utm_campaign
            if camp not in campaign_data:
                campaign_data[camp] = {"visits": 0, "orders": 0, "revenue": 0}
            campaign_data[camp]["orders"] += 1
            campaign_data[camp]["revenue"] += o.total

    # Estimate visits per channel from order attribution ratios
    total_orders = len(orders)
    for _ch, d in channel_data.items():
        ratio = d["orders"] / total_orders if total_orders > 0 else 0
        d["visits"] = max(d["orders"], int(total_visits * ratio))

    for _camp, d in campaign_data.items():
        ratio = d["orders"] / total_orders if total_orders > 0 else 0
        d["visits"] = max(d["orders"], int(total_visits * ratio))

    channels = [
        ChannelAttributionItem(
            channel=ch,
            visits=d["visits"],
            orders=d["orders"],
            revenue=d["revenue"],
            conversion_rate=round(d["orders"] / d["visits"] * 100, 1)
            if d["visits"] > 0
            else 0,
        )
        for ch, d in sorted(
            channel_data.items(), key=lambda x: x[1]["revenue"], reverse=True
        )
    ]

    campaigns = [
        CampaignItem(
            campaign=camp,
            visits=d["visits"],
            orders=d["orders"],
            revenue=d["revenue"],
        )
        for camp, d in sorted(
            campaign_data.items(), key=lambda x: x[1]["revenue"], reverse=True
        )
    ][:20]

    return SuccessResponse(
        data=MarketingAttributionResponse(
            channels=channels,
            campaigns=campaigns,
            total_visits=total_visits,
            attributed_visits=attributed_visits,
        ),
        message="Marketing attribution retrieved successfully",
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
    lang: str = Query("ar", description="Language: ar or en"),
):
    """Get AI-powered insights with anomaly detection and LLM narratives.

    Uses cached insights from store.settings when available (refreshed daily
    by Celery task). Falls back to live calculation if no cache exists.
    """
    from src.application.services.ai_insights_service import generate_insights

    # Try cached insights first
    if store.settings:
        cached = store.settings.get("ai_insights")
        if cached and cached.get("generated_at"):
            # Use cache if generated today
            from datetime import UTC, datetime

            try:
                gen_time = datetime.fromisoformat(cached["generated_at"])
                if gen_time.date() == datetime.now(UTC).date():
                    signals = [
                        InsightSignalResponse(**s) for s in cached.get("signals", [])
                    ]
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
    horizon: int = Query(30, ge=7, le=90, description="Forecast horizon in days"),
):
    """Get sales revenue forecast using Holt-Winters exponential smoothing."""
    from src.application.services.forecast_service import generate_forecast

    today = date.today()
    date_from = today - timedelta(days=365)  # Use up to 1 year of data
    rollups = await rollup_repo.get_range(store.id, date_from, today)

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
    days: int = Query(7, ge=1, le=30),
    has_order: bool = Query(False, description="Filter to sessions with orders"),
    min_pages: int = Query(1, ge=1, description="Minimum page count"),
    device: str = Query("", description="Filter by device: mobile, desktop, tablet"),
):
    """Get session list with duration, pages, funnel reached, and device type."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

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

    # Get sessions that completed an order
    order_sessions = await funnel_repo.get_sessions_with_step(
        store.id, period_start, now, "checkout_started"
    )

    # Get deepest funnel step per session
    funnel_steps_order = [
        "page_view",
        "product_view",
        "add_to_cart",
        "checkout_started",
        "order_completed",
        "order_delivered",
    ]
    # Batch: get all funnel step sets
    step_sessions: dict[str, set[str]] = {}
    for step in funnel_steps_order[1:]:  # skip page_view, all have it
        step_sessions[step] = await funnel_repo.get_sessions_with_step(
            store.id, period_start, now, step
        )

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
        in_orders = fp in order_sessions

        # Deepest funnel step
        deepest = "page_view"
        for step in funnel_steps_order[1:]:
            if fp in step_sessions.get(step, set()):
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
