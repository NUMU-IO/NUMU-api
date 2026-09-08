"""Admin dashboard statistics endpoints.

URL: /api/v1/admin/dashboard
Requires SUPER_ADMIN role.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.marketplace_theme import MarketplaceVersionStatus
from src.core.entities.order import OrderStatus, PaymentStatus
from src.core.entities.plan import PLAN_LIMITS
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentProofModel,
)
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.public.wallet import WalletTopupProofModel
from src.infrastructure.database.models.public.whatsapp_access import (
    WhatsAppAccessRequestModel,
    WhatsAppAccessStatus,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeVersionModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel

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


class QueueCountsResponse(BaseModel):
    """Depth of every queue an operator can actually work.

    Drives the sidebar badges and the overview triage strip. Counts only —
    each number links to the page that can clear it.
    """

    whatsapp_access: int
    marketplace_review: int
    risk_review: int
    support_cases: int
    subscription_payments: int
    wallet_topups: int
    payment_failed_orders: int
    unfulfilled_orders: int
    read_only_tenants: int
    trialing_tenants: int
    total: int


class SearchHit(BaseModel):
    """One command-palette result."""

    id: str
    kind: Literal["store", "order", "customer", "merchant"]
    label: str
    meta: str | None = None
    href: str


class SearchResponse(BaseModel):
    hits: list[SearchHit]


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
        TenantModel.lifecycle_state != TenantLifecycleState.DEMO.value
    ) & (TenantModel.is_internal.is_(False))

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

    starter_monthly_mrr = (
        starter_monthly_count * starter_features.monthly_price_piasters
    )
    starter_annual_mrr = starter_annual_count * (
        starter_features.annual_price_piasters // 12
    )
    pro_monthly_mrr = pro_monthly_count * pro_features.monthly_price_piasters
    pro_annual_mrr = pro_annual_count * (pro_features.annual_price_piasters // 12)
    total_mrr = (
        starter_monthly_mrr + starter_annual_mrr + pro_monthly_mrr + pro_annual_mrr
    )

    mrr = MRRBreakdown(
        total=total_mrr,
        starter_monthly=starter_monthly_mrr,
        starter_annual=starter_annual_mrr,
        pro_monthly=pro_monthly_mrr,
        pro_annual=pro_annual_mrr,
        subscriber_count=(
            starter_monthly_count
            + starter_annual_count
            + pro_monthly_count
            + pro_annual_count
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


@router.get(
    "/queues",
    response_model=SuccessResponse[QueueCountsResponse],
    summary="Get open queue depths for the admin shell",
    operation_id="admin_dashboard_queues",
)
async def get_queue_counts(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Count everything waiting on a human, in one round trip.

    The sidebar renders a badge per queue and the overview strip renders the
    same numbers as tiles, so both read from here rather than each page
    fetching its own list just to count the rows.

    Demo and internal tenants are excluded from the tenant-scoped counts for
    the same reason they are excluded from ``/stats``: they are not merchants.
    """
    excluded_tenant_ids = (
        select(TenantModel.id)
        .where(
            (TenantModel.lifecycle_state == TenantLifecycleState.DEMO.value)
            | (TenantModel.is_internal.is_(True))
        )
        .scalar_subquery()
    )

    async def _count(stmt) -> int:
        return (await db.execute(stmt)).scalar() or 0

    whatsapp_access = await _count(
        select(func.count(WhatsAppAccessRequestModel.id)).where(
            WhatsAppAccessRequestModel.status == WhatsAppAccessStatus.PENDING
        )
    )
    marketplace_review = await _count(
        select(func.count(MarketplaceThemeVersionModel.id)).where(
            MarketplaceThemeVersionModel.status
            == MarketplaceVersionStatus.PENDING_REVIEW.value
        )
    )
    subscription_payments = await _count(
        select(func.count(SubscriptionPaymentProofModel.id)).where(
            SubscriptionPaymentProofModel.status == "awaiting_review"
        )
    )
    wallet_topups = await _count(
        select(func.count(WalletTopupProofModel.id)).where(
            WalletTopupProofModel.status == "awaiting_review"
        )
    )
    risk_review = await _count(
        select(func.count())
        .select_from(text("public.risk_assessments r"))
        .where(text("r.action_taken IS NULL AND r.risk_level IN ('high','critical')"))
    )
    support_cases = await _count(
        select(func.count())
        .select_from(text("public.support_cases c"))
        .where(text("c.status IN ('open','pending_merchant')"))
    )
    payment_failed_orders = await _count(
        select(func.count(OrderModel.id)).where(
            OrderModel.status == OrderStatus.PAYMENT_FAILED,
            OrderModel.tenant_id.notin_(excluded_tenant_ids),
        )
    )
    # "Paid for, and nobody has moved it in two days" — the orders a support
    # agent chases before the merchant's customer does.
    unfulfilled_orders = await _count(
        select(func.count(OrderModel.id)).where(
            OrderModel.status.in_([
                OrderStatus.PENDING,
                OrderStatus.CONFIRMED,
                OrderStatus.PROCESSING,
            ]),
            OrderModel.created_at < datetime.now(UTC) - timedelta(hours=48),
            OrderModel.tenant_id.notin_(excluded_tenant_ids),
        )
    )
    read_only_tenants = await _count(
        select(func.count(TenantModel.id)).where(
            TenantModel.lifecycle_state == TenantLifecycleState.READ_ONLY.value,
            TenantModel.is_internal.is_(False),
        )
    )
    trialing_tenants = await _count(
        select(func.count(TenantModel.id)).where(
            TenantModel.lifecycle_state == TenantLifecycleState.TRIAL.value,
            TenantModel.is_internal.is_(False),
        )
    )

    return SuccessResponse(
        data=QueueCountsResponse(
            whatsapp_access=whatsapp_access,
            marketplace_review=marketplace_review,
            risk_review=risk_review,
            support_cases=support_cases,
            subscription_payments=subscription_payments,
            wallet_topups=wallet_topups,
            payment_failed_orders=payment_failed_orders,
            unfulfilled_orders=unfulfilled_orders,
            read_only_tenants=read_only_tenants,
            trialing_tenants=trialing_tenants,
            # Only the four review queues — the order and lifecycle counts are
            # context, not work items, and would inflate the sidebar badge.
            total=(
                whatsapp_access
                + marketplace_review
                + subscription_payments
                + wallet_topups
                + risk_review
                + support_cases
            ),
        ),
        message="Queue counts retrieved successfully",
    )


@router.get(
    "/search",
    response_model=SuccessResponse[SearchResponse],
    summary="Cross-entity search for the admin command palette",
    operation_id="admin_dashboard_search",
)
async def search(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(min_length=2, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
):
    """Find a merchant, store, order or customer from one query string.

    The operator does not know in advance which entity type they hold — they
    have a phone number, an order number, or half a domain. Each entity type
    is searched with the pattern that actually identifies it, and every hit
    carries the admin URL that opens it.
    """
    term = q.strip()
    like = f"%{term.lower()}%"
    hits: list[SearchHit] = []

    merchants = (
        await db.execute(
            select(TenantModel.id, TenantModel.name, TenantModel.subdomain)
            .where(
                TenantModel.is_internal.is_(False),
                or_(
                    func.lower(TenantModel.name).like(like),
                    func.lower(TenantModel.subdomain).like(like),
                ),
            )
            .order_by(TenantModel.name)
            .limit(limit)
        )
    ).all()
    hits += [
        SearchHit(
            id=str(tenant_id),
            kind="merchant",
            label=name,
            meta=subdomain,
            href=f"/merchants/{tenant_id}",
        )
        for tenant_id, name, subdomain in merchants
    ]

    stores = (
        await db.execute(
            select(
                StoreModel.id,
                StoreModel.tenant_id,
                StoreModel.name,
                StoreModel.subdomain,
                StoreModel.custom_domain,
            )
            .where(
                or_(
                    func.lower(StoreModel.name).like(like),
                    func.lower(StoreModel.subdomain).like(like),
                    func.lower(StoreModel.custom_domain).like(like),
                )
            )
            .order_by(StoreModel.name)
            .limit(limit)
        )
    ).all()
    hits += [
        SearchHit(
            id=str(store_id),
            kind="store",
            label=name,
            meta=custom_domain or subdomain,
            href=f"/merchants/{tenant_id}",
        )
        for store_id, tenant_id, name, subdomain, custom_domain in stores
    ]

    orders = (
        await db.execute(
            select(OrderModel.id, OrderModel.order_number, OrderModel.status)
            .where(func.lower(OrderModel.order_number).like(like))
            .order_by(OrderModel.created_at.desc())
            .limit(limit)
        )
    ).all()
    hits += [
        SearchHit(
            id=str(order_id),
            kind="order",
            label=number,
            meta=str(getattr(order_status, "value", order_status)),
            href=f"/orders?q={number}",
        )
        for order_id, number, order_status in orders
    ]

    customers = (
        await db.execute(
            select(
                CustomerModel.id,
                CustomerModel.first_name,
                CustomerModel.last_name,
                CustomerModel.email,
                CustomerModel.phone,
            )
            .where(
                or_(
                    func.lower(CustomerModel.email).like(like),
                    CustomerModel.phone.like(f"%{term}%"),
                    func.lower(
                        CustomerModel.first_name + " " + CustomerModel.last_name
                    ).like(like),
                )
            )
            .order_by(CustomerModel.created_at.desc())
            .limit(limit)
        )
    ).all()
    hits += [
        SearchHit(
            id=str(customer_id),
            kind="customer",
            label=f"{first} {last}".strip() or email,
            meta=phone or email,
            href=f"/customers?q={email}",
        )
        for customer_id, first, last, email, phone in customers
    ]

    return SuccessResponse(data=SearchResponse(hits=hits), message="Search completed")
