"""Admin dashboard statistics endpoints.

URL: /api/v1/admin/dashboard
Requires SUPER_ADMIN role.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.order import PaymentStatus
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class DashboardStatsResponse(BaseModel):
    total_revenue: int
    total_orders: int
    total_customers: int
    active_merchants: int
    revenue_change: float
    orders_change: float
    customers_change: float
    merchants_change: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pct_change(current: int | float, previous: int | float) -> float:
    """Calculate percentage change, returning 0 if previous is 0."""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 1)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/stats",
    response_model=SuccessResponse[DashboardStatsResponse],
    summary="Get platform dashboard statistics",
    operation_id="admin_dashboard_stats",
)
async def get_dashboard_stats(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get aggregated platform-wide statistics with month-over-month changes."""
    now = datetime.now(UTC)
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_start = (current_month_start - timedelta(days=1)).replace(day=1)

    # --- Total revenue (paid orders) ---
    rev_result = await db.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.payment_status == PaymentStatus.PAID
        )
    )
    total_revenue = rev_result.scalar() or 0

    # Revenue this month
    rev_current = await db.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.payment_status == PaymentStatus.PAID,
            OrderModel.created_at >= current_month_start,
        )
    )
    rev_this_month = rev_current.scalar() or 0

    # Revenue last month
    rev_prev = await db.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.payment_status == PaymentStatus.PAID,
            OrderModel.created_at >= prev_month_start,
            OrderModel.created_at < current_month_start,
        )
    )
    rev_last_month = rev_prev.scalar() or 0

    # --- Total orders ---
    orders_total_result = await db.execute(select(func.count(OrderModel.id)))
    total_orders = orders_total_result.scalar() or 0

    orders_current = await db.execute(
        select(func.count(OrderModel.id)).where(
            OrderModel.created_at >= current_month_start
        )
    )
    orders_this_month = orders_current.scalar() or 0

    orders_prev = await db.execute(
        select(func.count(OrderModel.id)).where(
            OrderModel.created_at >= prev_month_start,
            OrderModel.created_at < current_month_start,
        )
    )
    orders_last_month = orders_prev.scalar() or 0

    # --- Total customers ---
    cust_total_result = await db.execute(select(func.count(CustomerModel.id)))
    total_customers = cust_total_result.scalar() or 0

    cust_current = await db.execute(
        select(func.count(CustomerModel.id)).where(
            CustomerModel.created_at >= current_month_start
        )
    )
    cust_this_month = cust_current.scalar() or 0

    cust_prev = await db.execute(
        select(func.count(CustomerModel.id)).where(
            CustomerModel.created_at >= prev_month_start,
            CustomerModel.created_at < current_month_start,
        )
    )
    cust_last_month = cust_prev.scalar() or 0

    # --- Active merchants (tenants) ---
    merchants_result = await db.execute(
        select(func.count(TenantModel.id)).where(TenantModel.is_active.is_(True))
    )
    active_merchants = merchants_result.scalar() or 0

    merchants_current = await db.execute(
        select(func.count(TenantModel.id)).where(
            TenantModel.is_active.is_(True),
            TenantModel.created_at >= current_month_start,
        )
    )
    merchants_this_month = merchants_current.scalar() or 0

    merchants_prev = await db.execute(
        select(func.count(TenantModel.id)).where(
            TenantModel.is_active.is_(True),
            TenantModel.created_at >= prev_month_start,
            TenantModel.created_at < current_month_start,
        )
    )
    merchants_last_month = merchants_prev.scalar() or 0

    return SuccessResponse(
        data=DashboardStatsResponse(
            total_revenue=total_revenue,
            total_orders=total_orders,
            total_customers=total_customers,
            active_merchants=active_merchants,
            revenue_change=_pct_change(rev_this_month, rev_last_month),
            orders_change=_pct_change(orders_this_month, orders_last_month),
            customers_change=_pct_change(cust_this_month, cust_last_month),
            merchants_change=_pct_change(merchants_this_month, merchants_last_month),
        ),
        message="Dashboard stats retrieved successfully",
    )
