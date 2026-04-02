"""Analytics routes nested under stores.

URL: /stores/{store_id}/analytics
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.dependencies import (
    get_customer_repository,
    get_order_repository,
    get_shipment_repository,
    verify_store_ownership,
)
from src.api.dependencies.repositories import get_page_view_repository
from src.api.responses import SuccessResponse
from src.application.services.health_score_service import calculate_store_health_score
from src.core.entities.order import PaymentStatus
from src.core.entities.store import Store
from src.infrastructure.repositories import (
    CustomerRepository,
    OrderRepository,
    StoreRepository,
)
from src.infrastructure.repositories.page_view_repository import PageViewRepository
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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(30, ge=1, le=365, description="Number of days"),
):
    """Get sales overview for the store."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)
    previous_period_start = period_start - timedelta(days=days)

    # Current period
    current_revenue = await order_repo.get_revenue_by_date_range(
        store.id, period_start, now
    )
    current_orders = await order_repo.count_by_store(
        store.id, date_from=period_start, date_to=now
    )

    # Previous period for comparison
    previous_revenue = await order_repo.get_revenue_by_date_range(
        store.id, previous_period_start, period_start
    )
    previous_orders = await order_repo.count_by_store(
        store.id, date_from=previous_period_start, date_to=period_start
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

    # Average order value
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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(30, ge=1, le=365, description="Number of days"),
):
    """Get sales data for chart visualization."""
    now = datetime.now(UTC)
    data_points = []

    for i in range(days - 1, -1, -1):
        day_end = now - timedelta(days=i)
        day_start = day_end - timedelta(days=1)

        revenue = await order_repo.get_revenue_by_date_range(
            store.id, day_start, day_end
        )
        orders = await order_repo.get_by_date_range(store.id, day_start, day_end)

        data_points.append(
            SalesDataPointResponse(
                date=day_start.strftime("%b %d"),
                sales=revenue,
                orders=len(orders),
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
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
):
    """Get top selling products for the store."""
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store.id, period_start, now, limit=1000)

    # Aggregate by product
    product_sales: dict[UUID, dict] = {}
    total_revenue = 0

    for order in orders:
        if order.payment_status not in [
            PaymentStatus.PAID,
            PaymentStatus.PARTIALLY_REFUNDED,
        ]:
            continue

        for item in order.line_items:
            if item.product_id not in product_sales:
                product_sales[item.product_id] = {
                    "id": item.product_id,
                    "name": item.product_name,
                    "sku": item.sku,
                    "quantity": 0,
                    "revenue": 0,
                }
            product_sales[item.product_id]["quantity"] += item.quantity
            product_sales[item.product_id]["revenue"] += item.total_price
            total_revenue += item.total_price

    # Sort and take top N
    sorted_products = sorted(
        product_sales.values(),
        key=lambda x: x["revenue"],
        reverse=True,
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
