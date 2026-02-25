"""Dashboard routes nested under stores.

URL: /stores/{store_id}/dashboard
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.dependencies import (
    get_customer_repository,
    get_order_repository,
    get_product_repository,
    get_store_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.application.use_cases.stores import GetDashboardStatsUseCase
from src.core.entities.store import Store
from src.infrastructure.repositories import (
    CustomerRepository,
    OrderRepository,
    ProductRepository,
    StoreRepository,
)

router = APIRouter(prefix="/{store_id}/dashboard")


class DashboardStatsResponse(BaseModel):
    """Dashboard statistics response."""

    # Revenue
    total_revenue: int
    revenue_change_percent: float
    avg_order_value: int
    currency: str

    # Orders
    total_orders: int
    pending_orders: int
    confirmed_orders: int
    processing_orders: int
    shipped_orders: int
    completed_orders: int
    cancelled_orders: int

    # Customers
    total_customers: int
    new_customers: int

    # Products
    total_products: int
    low_stock_count: int

    # Period
    period_start: str
    period_end: str


class RevenueDataPointResponse(BaseModel):
    """Revenue data point for chart."""

    date: str
    revenue: int
    orders: int


class DashboardTopProductResponse(BaseModel):
    """Top product response (dashboard view)."""

    id: str
    name: str
    sku: str | None
    quantity_sold: int
    revenue: int


@router.get(
    "/stats",
    response_model=SuccessResponse[DashboardStatsResponse],
    summary="Get dashboard statistics",
    operation_id="get_dashboard_stats",
)
async def get_dashboard_stats(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    days: int = Query(30, ge=1, le=365, description="Number of days for the period"),
):
    """Get dashboard statistics for the store."""
    use_case = GetDashboardStatsUseCase(
        order_repository=order_repo,
        customer_repository=customer_repo,
        product_repository=product_repo,
        store_repository=store_repo,
    )

    result = await use_case.execute(
        store_id=store.id,
        user_id=store.owner_id,
        days=days,
    )

    return SuccessResponse(
        data=DashboardStatsResponse(
            total_revenue=result.total_revenue,
            revenue_change_percent=result.revenue_change_percent,
            avg_order_value=result.avg_order_value,
            currency=result.currency,
            total_orders=result.total_orders,
            pending_orders=result.pending_orders,
            confirmed_orders=result.confirmed_orders,
            processing_orders=result.processing_orders,
            shipped_orders=result.shipped_orders,
            completed_orders=result.completed_orders,
            cancelled_orders=result.cancelled_orders,
            total_customers=result.total_customers,
            new_customers=result.new_customers,
            total_products=result.total_products,
            low_stock_count=result.low_stock_count,
            period_start=str(result.period_start),
            period_end=str(result.period_end),
        ),
        message="Dashboard stats retrieved successfully",
    )


@router.get(
    "/revenue",
    response_model=SuccessResponse[list[RevenueDataPointResponse]],
    summary="Get revenue chart data",
    operation_id="get_revenue_chart",
)
async def get_revenue_chart(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    days: int = Query(30, ge=1, le=365, description="Number of days for the chart"),
):
    """Get revenue data for chart visualization."""
    use_case = GetDashboardStatsUseCase(
        order_repository=order_repo,
        customer_repository=customer_repo,
        product_repository=product_repo,
        store_repository=store_repo,
    )

    result = await use_case.get_revenue_chart(
        store_id=store.id,
        user_id=store.owner_id,
        days=days,
    )

    return SuccessResponse(
        data=[
            RevenueDataPointResponse(
                date=point.date,
                revenue=point.revenue,
                orders=point.orders,
            )
            for point in result
        ],
        message="Revenue data retrieved successfully",
    )


@router.get(
    "/top-products",
    response_model=SuccessResponse[list[DashboardTopProductResponse]],
    summary="Get top selling products (dashboard)",
    operation_id="get_dashboard_top_products",
)
async def get_dashboard_top_products(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    limit: int = Query(5, ge=1, le=20, description="Number of products to return"),
):
    """Get top selling products for the store."""
    use_case = GetDashboardStatsUseCase(
        order_repository=order_repo,
        customer_repository=customer_repo,
        product_repository=product_repo,
        store_repository=store_repo,
    )

    result = await use_case.get_top_products(
        store_id=store.id,
        user_id=store.owner_id,
        limit=limit,
    )

    return SuccessResponse(
        data=[
            DashboardTopProductResponse(
                id=product.id,
                name=product.name,
                sku=product.sku,
                quantity_sold=product.quantity_sold,
                revenue=product.revenue,
            )
            for product in result
        ],
        message="Top products retrieved successfully",
    )
