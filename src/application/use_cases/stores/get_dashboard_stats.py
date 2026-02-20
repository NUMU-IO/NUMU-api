"""Get dashboard stats use case."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from src.core.entities.order import OrderStatus, PaymentStatus
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


@dataclass
class DashboardStatsDTO:
    """Dashboard statistics data transfer object."""

    # Revenue
    total_revenue: int  # In cents
    revenue_change_percent: float  # Compared to previous period
    avg_order_value: int  # In cents (revenue / paid-orders in the period)
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
    new_customers: int  # In the period

    # Products
    total_products: int
    low_stock_count: int

    # Period info
    period_start: datetime
    period_end: datetime


@dataclass
class RevenueDataPoint:
    """Single data point for revenue chart."""

    date: str
    revenue: int
    orders: int


@dataclass
class TopProductDTO:
    """Top product data transfer object."""

    id: str
    name: str
    sku: str | None
    quantity_sold: int
    revenue: int


class GetDashboardStatsUseCase:
    """Use case for getting dashboard statistics."""

    def __init__(
        self,
        order_repository: IOrderRepository,
        customer_repository: ICustomerRepository,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.order_repository = order_repository
        self.customer_repository = customer_repository
        self.product_repository = product_repository
        self.store_repository = store_repository

    async def execute(
        self,
        store_id: UUID,
        user_id: UUID,
        days: int = 30,
    ) -> DashboardStatsDTO:
        """Get dashboard statistics for a store."""
        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to view this store's dashboard"
            )

        now = datetime.now(UTC)
        period_start = now - timedelta(days=days)
        previous_period_start = period_start - timedelta(days=days)

        # Get current period revenue
        current_revenue = await self.order_repository.get_revenue_by_date_range(
            store_id, period_start, now
        )

        # Get previous period revenue for comparison
        previous_revenue = await self.order_repository.get_revenue_by_date_range(
            store_id, previous_period_start, period_start
        )

        # Calculate revenue change percentage
        if previous_revenue > 0:
            revenue_change_percent = (
                (current_revenue - previous_revenue) / previous_revenue
            ) * 100
        elif current_revenue > 0:
            revenue_change_percent = 100.0
        else:
            revenue_change_percent = 0.0

        # Get order counts by status (filtered to the period)
        total_orders = await self.order_repository.count_by_store(
            store_id, date_from=period_start, date_to=now
        )
        pending_orders = await self.order_repository.count_by_store(
            store_id, OrderStatus.PENDING, date_from=period_start, date_to=now
        )
        confirmed_orders = await self.order_repository.count_by_store(
            store_id, OrderStatus.CONFIRMED, date_from=period_start, date_to=now
        )
        processing_orders = await self.order_repository.count_by_store(
            store_id, OrderStatus.PROCESSING, date_from=period_start, date_to=now
        )
        shipped_orders = await self.order_repository.count_by_store(
            store_id, OrderStatus.SHIPPED, date_from=period_start, date_to=now
        )
        completed_orders = await self.order_repository.count_by_store(
            store_id, OrderStatus.DELIVERED, date_from=period_start, date_to=now
        )
        cancelled_orders = await self.order_repository.count_by_store(
            store_id, OrderStatus.CANCELLED, date_from=period_start, date_to=now
        )

        # Get customer counts
        total_customers = await self.customer_repository.count_by_store(store_id)
        new_customers = await self.customer_repository.count_by_store(
            store_id, date_from=period_start
        )

        # Get product stats
        total_products = await self.product_repository.count_by_store(store_id)
        low_stock_products = await self.product_repository.get_low_stock(store_id)
        low_stock_count = len(low_stock_products)

        # Avg order value = revenue / orders (for the period), 0 if no orders
        avg_order_value = (
            round(current_revenue / total_orders) if total_orders > 0 else 0
        )

        return DashboardStatsDTO(
            total_revenue=current_revenue,
            revenue_change_percent=round(revenue_change_percent, 1),
            avg_order_value=avg_order_value,
            currency=store.default_currency or "EGP",
            total_orders=total_orders,
            pending_orders=pending_orders,
            confirmed_orders=confirmed_orders,
            processing_orders=processing_orders,
            shipped_orders=shipped_orders,
            completed_orders=completed_orders,
            cancelled_orders=cancelled_orders,
            total_customers=total_customers,
            new_customers=new_customers,
            total_products=total_products,
            low_stock_count=low_stock_count,
            period_start=period_start,
            period_end=now,
        )

    async def get_revenue_chart(
        self,
        store_id: UUID,
        user_id: UUID,
        days: int = 30,
    ) -> list[RevenueDataPoint]:
        """Get revenue data for chart visualization."""
        # Verify permissions
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to view this store's dashboard"
            )

        now = datetime.now(UTC)
        data_points = []

        # Get daily data for the period
        for i in range(days - 1, -1, -1):
            day_end = now - timedelta(days=i)
            day_start = day_end - timedelta(days=1)

            revenue = await self.order_repository.get_revenue_by_date_range(
                store_id, day_start, day_end
            )

            # Get orders for the day
            orders = await self.order_repository.get_by_date_range(
                store_id, day_start, day_end
            )

            data_points.append(
                RevenueDataPoint(
                    date=day_start.strftime("%Y-%m-%d"),
                    revenue=revenue,
                    orders=len(orders),
                )
            )

        return data_points

    async def get_top_products(
        self,
        store_id: UUID,
        user_id: UUID,
        limit: int = 5,
    ) -> list[TopProductDTO]:
        """Get top selling products for the store."""
        # Verify permissions
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to view this store's dashboard"
            )

        # Get recent orders to aggregate product sales
        now = datetime.now(UTC)
        period_start = now - timedelta(days=30)

        orders = await self.order_repository.get_by_date_range(
            store_id, period_start, now, limit=1000
        )

        # Aggregate by product
        product_sales: dict[UUID, dict] = {}
        for order in orders:
            # Only count completed/paid orders
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

        # Sort by quantity sold and take top N
        sorted_products = sorted(
            product_sales.values(),
            key=lambda x: x["quantity"],
            reverse=True,
        )[:limit]

        return [
            TopProductDTO(
                id=str(p["id"]),
                name=p["name"],
                sku=p["sku"],
                quantity_sold=p["quantity"],
                revenue=p["revenue"],
            )
            for p in sorted_products
        ]
