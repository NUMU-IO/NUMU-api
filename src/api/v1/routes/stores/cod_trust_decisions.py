"""COD trust decisions audit feed.

URL: /stores/{store_id}/cod-trust/decisions

Returns the merchant-facing list of COD trust filter decisions —
allowed, warned, and blocked — written by the storefront and merchant
order-creation paths. Lets merchants see what the filter has actually
been doing without leaving the payment setup page.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.risk_assessment import (
    RiskAssessmentModel,
)

router = APIRouter()


class CodTrustDecisionFactor(BaseModel):
    code: str
    weight: int = 0
    detail: str | None = None


class CodTrustDecisionItem(BaseModel):
    """A single decision shown in the merchant audit feed."""

    id: str
    order_id: str | None
    order_number: str | None
    created_at: str
    risk_score: int
    risk_level: str
    action_taken: str | None
    suggested_action: str | None
    factors: list[CodTrustDecisionFactor]
    phone_last4: str | None


class CodTrustDecisionsResponse(BaseModel):
    items: list[CodTrustDecisionItem]
    total: int
    limit: int
    offset: int


class TrustStatsResponse(BaseModel):
    """Per-store COD trust impact over a rolling window. Merchant-facing —
    a store only ever sees its own numbers (never the network-wide moat
    metrics, which are internal)."""

    period_days: int
    screened: int  # COD orders the trust filter evaluated
    high_risk: int  # blocked + warned (+ recover-flagged)
    blocked: int  # hard-stopped at checkout
    warned: int  # allowed but flagged
    recovered: int  # COD → prepaid conversions via the /pay recover flow


@router.get(
    "/{store_id}/cod-trust/stats",
    response_model=SuccessResponse[TrustStatsResponse],
    summary="Per-store COD trust impact stats",
    operation_id="get_cod_trust_stats",
)
async def get_cod_trust_stats(
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
    period_days: int = Query(30, ge=1, le=365),
):
    """Aggregate the store's own COD trust decisions over the last
    ``period_days``. Same scoping as the decisions feed (cod + cod_trust
    author), counted by action, plus the recover-flow conversions read
    from ``order.extra_data.cod_recovered``."""
    cutoff = datetime.now(UTC) - timedelta(days=period_days)

    base_filter = (
        (RiskAssessmentModel.store_id == store.id)
        & (RiskAssessmentModel.payment_method == "cod")
        & (RiskAssessmentModel.action_taken_by == "cod_trust")
        & (RiskAssessmentModel.action_taken.is_not(None))
        & (RiskAssessmentModel.created_at >= cutoff)
    )

    rows = await session.execute(
        select(RiskAssessmentModel.action_taken, func.count())
        .where(base_filter)
        .group_by(RiskAssessmentModel.action_taken)
    )
    by_action = dict(rows.all())
    screened = sum(by_action.values())
    blocked = by_action.get("blocked_high_risk", 0)
    warned = by_action.get("warned_high_risk", 0)
    recover_flagged = sum(c for a, c in by_action.items() if a and "recover" in a)
    high_risk = blocked + warned + recover_flagged

    recovered_q = await session.execute(
        select(func.count())
        .select_from(OrderModel)
        .where(
            (OrderModel.store_id == store.id)
            & (OrderModel.extra_data["cod_recovered"].astext == "true")
            & (OrderModel.created_at >= cutoff)
        )
    )
    recovered = recovered_q.scalar() or 0

    return SuccessResponse(
        data=TrustStatsResponse(
            period_days=period_days,
            screened=screened,
            high_risk=high_risk,
            blocked=blocked,
            warned=warned,
            recovered=recovered,
        ),
        message="COD trust stats retrieved",
    )


@router.get(
    "/{store_id}/cod-trust/decisions",
    response_model=SuccessResponse[CodTrustDecisionsResponse],
    summary="List COD trust decisions for the store",
    operation_id="list_cod_trust_decisions",
)
async def list_cod_trust_decisions(
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Return COD trust filter decisions for the merchant's store.

    Narrowed three ways so the feed only shows actionable COD trust
    decisions, not adjacent risk-assessment noise:

      1. ``payment_method='cod'`` — keeps Shopify online-payment risk
         scores out of the COD audit view.
      2. ``action_taken_by='cod_trust'`` — distinguishes rows the COD
         trust filter wrote (which always set this) from rows the
         general fraud-detection service writes (which doesn't, and
         uses a different ``factors`` schema). Without this guard the
         merchant sees rows with empty ``Action`` / ``Signals``
         columns and assumes the filter is broken.
      3. ``action_taken IS NOT NULL`` — defensive belt to skip any
         legacy / partial rows that slipped through before the
         ``action_taken_by`` column was populated consistently.

    We do a small left-outer join against orders to pull a phone
    last-4 for display when the order_id is set; blocked decisions
    (no order) show ``"—"`` instead.
    """
    base_filter = (
        (RiskAssessmentModel.store_id == store.id)
        & (RiskAssessmentModel.payment_method == "cod")
        & (RiskAssessmentModel.action_taken_by == "cod_trust")
        & (RiskAssessmentModel.action_taken.is_not(None))
    )

    total_q = await session.execute(select(RiskAssessmentModel.id).where(base_filter))
    total = len(total_q.all())

    rows = await session.execute(
        select(RiskAssessmentModel, OrderModel)
        .outerjoin(OrderModel, OrderModel.id == RiskAssessmentModel.order_id)
        .where(base_filter)
        .order_by(RiskAssessmentModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    items: list[CodTrustDecisionItem] = []
    for assessment, order in rows.all():
        phone_last4: str | None = None
        if order is not None and order.shipping_address:
            phone = (
                order.shipping_address.get("phone")
                if isinstance(order.shipping_address, dict)
                else getattr(order.shipping_address, "phone", None)
            )
            if phone and len(phone) >= 4:
                phone_last4 = phone[-4:]

        factors = [
            CodTrustDecisionFactor(
                code=f.get("code") or "unknown",
                weight=int(f.get("weight") or 0),
                detail=f.get("detail"),
            )
            for f in (assessment.factors or [])
            if isinstance(f, dict)
        ]

        items.append(
            CodTrustDecisionItem(
                id=str(assessment.id),
                order_id=str(assessment.order_id) if assessment.order_id else None,
                order_number=assessment.order_number,
                created_at=assessment.created_at.isoformat()
                if assessment.created_at
                else "",
                risk_score=assessment.risk_score,
                risk_level=assessment.risk_level,
                action_taken=assessment.action_taken,
                suggested_action=assessment.suggested_action,
                factors=factors,
                phone_last4=phone_last4,
            )
        )

    return SuccessResponse(
        data=CodTrustDecisionsResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        ),
        message="COD trust decisions retrieved",
    )
