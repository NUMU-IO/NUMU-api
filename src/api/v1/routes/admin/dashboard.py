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
from src.core.entities.plan import PLAN_LIMITS
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class MRRBreakdown(BaseModel):
    """Monthly Recurring Revenue breakdown by plan."""

    total: int  # piasters
    starter_monthly: int
    starter_annual: int
    pro_monthly: int
    pro_annual: int
    subscriber_count: int


class DashboardStatsResponse(BaseModel):
    total_revenue: int
    total_orders: int
    total_customers: int
    active_merchants: int
    revenue_change: float
    orders_change: float
    customers_change: float
    merchants_change: float
    mrr: MRRBreakdown


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
    """Get aggregated platform-wide statistics with month-over-month changes.

    Demo tenants (``lifecycle_state == "demo"``) **and** internal tenants
    (``is_internal == true``) are excluded from every aggregate so the
    platform dashboard reflects real merchant activity only.
    """
    now = datetime.now(UTC)
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_start = (current_month_start - timedelta(days=1)).replace(day=1)

    # Subquery of tenant IDs to exclude: demo OR internal.
    excluded_tenant_ids = (
        select(TenantModel.id)
        .where(
            (TenantModel.lifecycle_state == TenantLifecycleState.DEMO.value)
            | (TenantModel.is_internal.is_(True))
        )
        .scalar_subquery()
    )
    not_excluded_order = OrderModel.tenant_id.notin_(excluded_tenant_ids)
    not_excluded_customer = CustomerModel.tenant_id.notin_(excluded_tenant_ids)
    not_excluded_tenant = (
        (TenantModel.lifecycle_state != TenantLifecycleState.DEMO.value)
        & (TenantModel.is_internal.is_(False))
    )

    # --- Total revenue (paid orders) ---
    rev_result = await db.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.payment_status == PaymentStatus.PAID,
            not_excluded_order,
        )
    )
    total_revenue = rev_result.scalar() or 0

    # Revenue this month
    rev_current = await db.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.payment_status == PaymentStatus.PAID,
            OrderModel.created_at >= current_month_start,
            not_excluded_order,
        )
    )
    rev_this_month = rev_current.scalar() or 0

    # Revenue last month
    rev_prev = await db.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.payment_status == PaymentStatus.PAID,
            OrderModel.created_at >= prev_month_start,
            OrderModel.created_at < current_month_start,
            not_excluded_order,
        )
    )
    rev_last_month = rev_prev.scalar() or 0

    # --- Total orders ---
    orders_total_result = await db.execute(
        select(func.count(OrderModel.id)).where(not_excluded_order)
    )
    total_orders = orders_total_result.scalar() or 0

    orders_current = await db.execute(
        select(func.count(OrderModel.id)).where(
            OrderModel.created_at >= current_month_start,
            not_excluded_order,
        )
    )
    orders_this_month = orders_current.scalar() or 0

    orders_prev = await db.execute(
        select(func.count(OrderModel.id)).where(
            OrderModel.created_at >= prev_month_start,
            OrderModel.created_at < current_month_start,
            not_excluded_order,
        )
    )
    orders_last_month = orders_prev.scalar() or 0

    # --- Total customers ---
    cust_total_result = await db.execute(
        select(func.count(CustomerModel.id)).where(not_excluded_customer)
    )
    total_customers = cust_total_result.scalar() or 0

    cust_current = await db.execute(
        select(func.count(CustomerModel.id)).where(
            CustomerModel.created_at >= current_month_start,
            not_excluded_customer,
        )
    )
    cust_this_month = cust_current.scalar() or 0

    cust_prev = await db.execute(
        select(func.count(CustomerModel.id)).where(
            CustomerModel.created_at >= prev_month_start,
            CustomerModel.created_at < current_month_start,
            not_excluded_customer,
        )
    )
    cust_last_month = cust_prev.scalar() or 0

    # --- Active merchants (tenants) ---
    merchants_result = await db.execute(
        select(func.count(TenantModel.id)).where(
            TenantModel.is_active.is_(True),
            not_excluded_tenant,
        )
    )
    active_merchants = merchants_result.scalar() or 0

    merchants_current = await db.execute(
        select(func.count(TenantModel.id)).where(
            TenantModel.is_active.is_(True),
            TenantModel.created_at >= current_month_start,
            not_excluded_tenant,
        )
    )
    merchants_this_month = merchants_current.scalar() or 0

    merchants_prev = await db.execute(
        select(func.count(TenantModel.id)).where(
            TenantModel.is_active.is_(True),
            TenantModel.created_at >= prev_month_start,
            TenantModel.created_at < current_month_start,
            not_excluded_tenant,
        )
    )
    merchants_last_month = merchants_prev.scalar() or 0

    # --- MRR (Monthly Recurring Revenue) ---
    # Query active, non-excluded subscribers grouped by plan + billing_cycle.
    mrr_result = await db.execute(
        select(
            TenantModel.plan,
            TenantModel.billing_cycle,
            func.count(TenantModel.id),
        )
        .where(
            TenantModel.lifecycle_state == TenantLifecycleState.ACTIVE.value,
            not_excluded_tenant,
            TenantModel.plan.in_(["starter", "pro"]),
        )
        .group_by(TenantModel.plan, TenantModel.billing_cycle)
    )

    starter_monthly_count = 0
    starter_annual_count = 0
    pro_monthly_count = 0
    pro_annual_count = 0

    for plan, cycle, cnt in mrr_result.all():
        if plan == "starter" and cycle == "annual":
            starter_annual_count = cnt
        elif plan == "starter":
            starter_monthly_count = cnt
        elif plan == "pro" and cycle == "annual":
            pro_annual_count = cnt
        elif plan == "pro":
            pro_monthly_count = cnt

    # Normalise annual plans to monthly equivalent for MRR.
    starter_features = PLAN_LIMITS["starter"]
    pro_features = PLAN_LIMITS["pro"]

    starter_monthly_mrr = starter_monthly_count * starter_features.monthly_price_piasters
    starter_annual_mrr = starter_annual_count * (starter_features.annual_price_piasters // 12)
    pro_monthly_mrr = pro_monthly_count * pro_features.monthly_price_piasters
    pro_annual_mrr = pro_annual_count * (pro_features.annual_price_piasters // 12)
    total_mrr = starter_monthly_mrr + starter_annual_mrr + pro_monthly_mrr + pro_annual_mrr

    mrr = MRRBreakdown(
        total=total_mrr,
        starter_monthly=starter_monthly_mrr,
        starter_annual=starter_annual_mrr,
        pro_monthly=pro_monthly_mrr,
        pro_annual=pro_annual_mrr,
        subscriber_count=(
            starter_monthly_count + starter_annual_count
            + pro_monthly_count + pro_annual_count
        ),
    )

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
            mrr=mrr,
        ),
        message="Dashboard stats retrieved successfully",
    )
