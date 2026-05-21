"""Dashboard overview endpoint."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from src.api.dependencies.shopify import (
    get_payment_link_session_repo,
    get_payment_transaction_repo,
    get_risk_assessment_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import DashboardOverviewResponse
from src.infrastructure.repositories.shopify_repository import (
    PaymentLinkSessionRepository,
    PaymentTransactionRepository,
    RiskAssessmentRepository,
)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


@router.get(
    "/{store_id}/dashboard/overview",
    response_model=SuccessResponse[DashboardOverviewResponse],
    summary="Get dashboard overview stats",
    operation_id="shopify_dashboard_overview",
)
async def dashboard_overview(
    store_id: Annotated[UUID, Path()],
    risk_repo: Annotated[RiskAssessmentRepository, Depends(get_risk_assessment_repo)],
    pt_repo: Annotated[
        PaymentTransactionRepository, Depends(get_payment_transaction_repo)
    ],
    pls_repo: Annotated[
        PaymentLinkSessionRepository, Depends(get_payment_link_session_repo)
    ],
    days: int = Query(30, ge=1, le=365),
):
    """Aggregate dashboard stats for the Shopify app home screen."""
    # Risk metrics via SQL aggregation (no in-memory loading)
    risk_stats = await risk_repo.aggregate_dashboard(store_id, days=days)

    # Payment channel aggregates for totals
    channels = await pt_repo.aggregate_channels(store_id, days=days)

    total_orders = 0
    total_cod_orders = 0
    total_revenue = 0
    cod_success = 0
    cod_total = 0

    for ch in channels:
        attempts = ch.get("total_attempts", 0) or 0
        success = ch.get("successful_raw", 0) or 0
        revenue = ch.get("revenue_cents", 0) or 0
        total_orders += attempts
        total_revenue += revenue
        if ch.get("channel") == "cod":
            total_cod_orders += attempts
            cod_success += success
            cod_total += attempts

    cod_success_rate = round((cod_success / cod_total * 100) if cod_total else 0.0, 1)

    # COD-to-Prepaid conversion metrics
    conversion = await pls_repo.aggregate_conversions(store_id, days=days)

    return SuccessResponse(
        data=DashboardOverviewResponse(
            cod_success_rate=cod_success_rate,
            revenue_protected_cents=risk_stats["revenue_protected_cents"],
            high_risk_orders_count=risk_stats["high_risk_orders_count"],
            payment_recovery_cents=risk_stats["payment_recovery_cents"],
            total_orders=total_orders or risk_stats["total_orders"],
            total_cod_orders=total_cod_orders,
            total_revenue_cents=total_revenue,
            period_days=days,
            conversion_links_sent=conversion["links_sent"],
            conversion_links_completed=conversion["links_completed"],
            conversion_rate=conversion["conversion_rate"],
            conversion_revenue_cents=conversion["conversion_revenue_cents"],
        ),
    )
