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
from src.core.utils.store_timezone import resolve_store_timezone_name, safe_zone
from src.infrastructure.database.order_status_filters import (
    NON_REVENUE_STATUSES_LC,
)


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

    # Gross profit over the SAME orders the revenue figure counts
    # (`exclude_non_revenue`), net of line and order discounts. Only
    # covers line items whose variant or product carries a cost_price;
    # the rest are excluded from both totals. Shipping, gateway fees and
    # platform commission are NOT deducted — this is gross margin, not
    # bottom-line net profit.
    total_profit: int  # In cents
    total_cogs: int  # In cents
    products_with_cost: int

    # Period info
    period_start: datetime
    period_end: datetime


@dataclass
class RevenueDataPoint:
    """Single data point for revenue chart."""

    date: str
    revenue: int
    orders: int
    visits: int = 0


@dataclass
class TopProductDTO:
    """Top product data transfer object."""

    id: str
    name: str
    sku: str | None
    quantity_sold: int
    revenue: int
    image_url: str | None = None


class GetDashboardStatsUseCase:
    """Use case for getting dashboard statistics."""

    def __init__(
        self,
        order_repository: IOrderRepository,
        customer_repository: ICustomerRepository,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
        variant_repository=None,
    ) -> None:
        self.order_repository = order_repository
        self.customer_repository = customer_repository
        self.product_repository = product_repository
        self.store_repository = store_repository
        # Optional so the chart / top-product call sites (which never
        # touch cost) can keep constructing this with four repositories.
        self.variant_repository = variant_repository

    async def execute(
        self,
        store_id: UUID,
        user_id: UUID,
        days: int = 30,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> DashboardStatsDTO:
        """Get dashboard statistics for a store.

        Accepts either an explicit ``[period_start, period_end]`` window
        (preferred — produced by the shared date-range dependency) OR a
        legacy ``days`` count.
        """
        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to view this store's dashboard"
            )

        if period_start is not None and period_end is not None:
            now = period_end
            span = period_end - period_start
            previous_period_start = period_start - span
        else:
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

        # Order counts by status for the period: one GROUP BY instead of
        # seven counts. The total leaves out unpaid card-gateway orders
        # (AWAITING_PAYMENT), as count_by_store always has.
        by_status = await self.order_repository.count_by_status_for_store(
            store_id, date_from=period_start, date_to=now
        )
        total_orders = sum(
            n for s, n in by_status.items() if s != OrderStatus.AWAITING_PAYMENT.value
        )
        pending_orders = by_status.get(OrderStatus.PENDING.value, 0)
        confirmed_orders = by_status.get(OrderStatus.CONFIRMED.value, 0)
        processing_orders = by_status.get(OrderStatus.PROCESSING.value, 0)
        shipped_orders = by_status.get(OrderStatus.SHIPPED.value, 0)
        completed_orders = by_status.get(OrderStatus.DELIVERED.value, 0)
        cancelled_orders = by_status.get(OrderStatus.CANCELLED.value, 0)

        # Get customer counts
        total_customers = await self.customer_repository.count_by_store(store_id)
        new_customers = await self.customer_repository.count_by_store(
            store_id, date_from=period_start
        )

        # Get product stats
        total_products = await self.product_repository.count_by_store(store_id)
        low_stock_count = await self.product_repository.count_low_stock(store_id)

        # Avg order value = revenue / orders (for the period), 0 if no orders
        avg_order_value = (
            round(current_revenue / total_orders) if total_orders > 0 else 0
        )

        # Profit / COGS aggregation. Only counts line items whose product
        # (or variant) has a cost_price set; the rest are excluded from
        # the totals and the UI nudges merchants to fill them in.
        #
        # Order set: the SAME definition the revenue tile uses
        # (`exclude_non_revenue`), NOT `payment_status == PAID`. On a
        # COD-dominant store most orders sit at payment_status PENDING
        # until the courier settles, so the old filter measured margin
        # over a subset of the revenue displayed right beside it and the
        # two numbers could never reconcile. `order_status_filters` is
        # the single platform answer to "is this revenue" — this was the
        # last aggregate that had not been migrated onto it.
        #
        # Cost lookup: a variant's own cost wins over its parent
        # product's, because the variant editor lets a merchant cost each
        # SKU separately and reading only `product.cost_price` silently
        # dropped every one of those lines.
        #
        # Line value: `total_price` (already net of line discounts) —
        # never `unit_price`, which is the list price. Same convention as
        # `Order.record_partial_acceptance`. Order-level discounts
        # (coupons) live on the order, so they are allocated to lines
        # pro-rata by share of subtotal, matching
        # `AnalyticsRepository.top_products`.
        # Only the columns the maths reads: whole orders and products used to be
        # loaded here (up to 5000 of each, with eager relationships), which
        # made this the slowest merchant endpoint.
        period_orders = await self.order_repository.get_profit_lines(
            store_id, period_start, now, limit=5000
        )
        product_cost_map = await self.product_repository.cost_cents_by_product(store_id)
        variant_cost_map: dict[UUID, int] = {}
        costed_product_ids = set(product_cost_map)
        if self.variant_repository is not None:
            for (
                variant_id,
                product_id,
                cents,
            ) in await self.variant_repository.cost_cents_by_variant(store_id):
                variant_cost_map[variant_id] = cents
                # "N of M products have a cost set" must agree with what the
                # profit maths can use: a variant-only cost covers the product.
                costed_product_ids.add(product_id)
        products_with_cost = len(costed_product_ids)

        total_cogs = 0
        total_profit = 0
        for status, subtotal, order_discount, line_items in period_orders:
            if status.lower() in NON_REVENUE_STATUSES_LC:
                continue
            for item in line_items:
                cost_cents = (
                    variant_cost_map.get(item.variant_id)
                    if item.variant_id is not None
                    else None
                )
                if cost_cents is None:
                    cost_cents = product_cost_map.get(item.product_id)
                if cost_cents is None:
                    continue
                # Legacy line items predate `total_price` and persist as
                # 0; fall back to list price rather than booking the
                # whole order as a loss.
                line_revenue = item.total_price or (item.unit_price * item.quantity)
                if order_discount and subtotal > 0:
                    line_revenue -= round(line_revenue / subtotal * order_discount)
                line_cogs = cost_cents * item.quantity
                total_cogs += line_cogs
                total_profit += line_revenue - line_cogs

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
            total_profit=total_profit,
            total_cogs=total_cogs,
            products_with_cost=products_with_cost,
            period_start=period_start,
            period_end=now,
        )

    async def get_revenue_chart(
        self,
        store_id: UUID,
        user_id: UUID,
        days: int = 30,
        page_view_repository=None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
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

        if period_start is not None and period_end is not None:
            now = period_end
        else:
            now = datetime.now(UTC)
            period_start = now - timedelta(days=days)

        # Store-local calendar days. The previous implementation issued
        # TWO queries per day (60 for a 30-day chart) over rolling 24h
        # windows labeled with the wrong date, while visits were keyed on
        # true calendar days — so revenue and visits on the same chart row
        # could describe different real days. One GROUP-BY-day query per
        # series, both bucketed on the store's wall clock, fixes the N+1
        # and the mismatch together.
        tz_name = resolve_store_timezone_name(getattr(store, "settings", None) or {})
        zone = safe_zone(tz_name)
        first_day = period_start.astimezone(zone).date()
        last_day = now.astimezone(zone).date()

        daily_visits_map: dict[str, int] = {}
        if page_view_repository:
            daily_visits = await page_view_repository.get_daily_visits(
                store_id, period_start, now, tz=tz_name
            )
            daily_visits_map = dict(daily_visits)

        rows = await self.order_repository.get_daily_aggregates(
            store_id, period_start, now, timezone=tz_name
        )
        by_day = {row[0]: row for row in rows}

        data_points = []
        d = first_day
        while d <= last_day:
            row = by_day.get(d)
            date_key = d.strftime("%Y-%m-%d")
            data_points.append(
                RevenueDataPoint(
                    date=date_key,
                    revenue=row[1] if row else 0,
                    orders=row[2] if row else 0,
                    visits=daily_visits_map.get(date_key, 0),
                )
            )
            d += timedelta(days=1)

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

        # Batch-fetch the products (only the top N) to attach the primary
        # image. Line items don't carry images, so resolve from the product.
        product_images: dict[UUID, str | None] = {}
        product_ids = [p["id"] for p in sorted_products if p["id"] is not None]
        if product_ids:
            products = await self.product_repository.get_by_ids(product_ids)
            product_images = {
                product.id: (product.images[0] if product.images else None)
                for product in products
            }

        return [
            TopProductDTO(
                id=str(p["id"]),
                name=p["name"],
                sku=p["sku"],
                quantity_sold=p["quantity"],
                revenue=p["revenue"],
                image_url=product_images.get(p["id"]),
            )
            for p in sorted_products
        ]
