"""Analytics routes nested under stores.

URL: /stores/{store_id}/analytics
"""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from src.api.dependencies import (
    get_customer_repository,
    get_order_repository,
    get_store_repository,
    require_store_owner,
)
from src.api.responses import SuccessResponse
from src.core.entities.order import PaymentStatus
from src.infrastructure.repositories import (
    CustomerRepository,
    OrderRepository,
    StoreRepository,
)

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


class TopProductResponse(BaseModel):
    """Top product by sales."""

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
)
async def get_sales_overview(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    days: int = Query(30, ge=1, le=365, description="Number of days"),
):
    """Get sales overview for the store."""
    # Verify store ownership
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    if store.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.utcnow()
    period_start = now - timedelta(days=days)
    previous_period_start = period_start - timedelta(days=days)

    # Current period
    current_revenue = await order_repo.get_revenue_by_date_range(store_id, period_start, now)
    current_orders = await order_repo.count_by_store(store_id)

    # Previous period for comparison
    previous_revenue = await order_repo.get_revenue_by_date_range(
        store_id, previous_period_start, period_start
    )
    previous_orders_list = await order_repo.get_by_date_range(
        store_id, previous_period_start, period_start
    )
    previous_orders = len(previous_orders_list)

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
            currency=store.default_currency or "EGP",
        ),
        message="Sales overview retrieved successfully",
    )


@router.get(
    "/sales-chart",
    response_model=SuccessResponse[list[SalesDataPointResponse]],
    summary="Get sales chart data",
)
async def get_sales_chart(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    days: int = Query(30, ge=1, le=365, description="Number of days"),
):
    """Get sales data for chart visualization."""
    # Verify store ownership
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    if store.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.utcnow()
    data_points = []

    for i in range(days - 1, -1, -1):
        day_end = now - timedelta(days=i)
        day_start = day_end - timedelta(days=1)

        revenue = await order_repo.get_revenue_by_date_range(store_id, day_start, day_end)
        orders = await order_repo.get_by_date_range(store_id, day_start, day_end)

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
    response_model=SuccessResponse[list[TopProductResponse]],
    summary="Get top selling products",
)
async def get_top_products(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
):
    """Get top selling products for the store."""
    # Verify store ownership
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    if store.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.utcnow()
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store_id, period_start, now, limit=1000)

    # Aggregate by product
    product_sales: dict[UUID, dict] = {}
    total_revenue = 0

    for order in orders:
        if order.payment_status not in [PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED]:
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
            TopProductResponse(
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
)
async def get_sales_by_location(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get sales breakdown by location/governorate."""
    # Verify store ownership
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    if store.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.utcnow()
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store_id, period_start, now, limit=1000)

    # Aggregate by location
    location_sales: dict[str, dict] = {}
    total_sales = 0

    for order in orders:
        if order.payment_status not in [PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED]:
            continue

        # Get location from shipping address
        location = "Unknown"
        if order.shipping_address:
            location = order.shipping_address.get("governorate") or \
                       order.shipping_address.get("city") or \
                       order.shipping_address.get("state") or "Unknown"

        if location not in location_sales:
            location_sales[location] = {"sales": 0, "orders": 0}

        location_sales[location]["sales"] += order.total_amount
        location_sales[location]["orders"] += 1
        total_sales += order.total_amount

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
)
async def get_customer_analytics(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get customer analytics for the store."""
    # Verify store ownership
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    if store.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.utcnow()
    period_start = now - timedelta(days=days)

    total_customers = await customer_repo.count_by_store(store_id)

    # Get orders to calculate metrics
    orders = await order_repo.get_by_date_range(store_id, period_start, now, limit=1000)

    # Count unique customers and their order frequency
    customer_orders: dict[UUID, int] = {}
    total_revenue = 0

    for order in orders:
        if order.customer_id:
            customer_orders[order.customer_id] = customer_orders.get(order.customer_id, 0) + 1
        total_revenue += order.total_amount

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
)
async def get_conversion_stats(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    days: int = Query(30, ge=1, le=365),
):
    """Get conversion statistics for the store."""
    # Verify store ownership
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    if store.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.utcnow()
    period_start = now - timedelta(days=days)

    orders = await order_repo.get_by_date_range(store_id, period_start, now, limit=1000)
    total_orders = len(orders)

    # These would typically come from analytics/tracking integration
    # For now, we estimate based on orders
    estimated_visitors = total_orders * 30  # Rough estimate: 3.3% conversion
    conversion_rate = (total_orders / estimated_visitors * 100) if estimated_visitors > 0 else 0

    # Cart abandonment - would need cart tracking
    # Estimate: industry average is ~70%
    cart_abandonment_rate = 70.0

    return SuccessResponse(
        data=ConversionStatsResponse(
            total_visitors=estimated_visitors,
            total_orders=total_orders,
            conversion_rate=round(conversion_rate, 2),
            cart_abandonment_rate=cart_abandonment_rate,
        ),
        message="Conversion stats retrieved successfully",
    )
