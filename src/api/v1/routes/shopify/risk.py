"""Risk scoring endpoints — list risk orders and take actions."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies.shopify import (
    get_risk_assessment_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import (
    RiskActionRequest,
    RiskOrderResponse,
)
from src.infrastructure.repositories.shopify_repository import RiskAssessmentRepository

router = APIRouter(dependencies=[Depends(verify_internal_key)])


@router.get(
    "/{store_id}/risk/orders",
    response_model=SuccessResponse[list[RiskOrderResponse]],
    summary="List risk-scored orders",
    operation_id="shopify_list_risk_orders",
)
async def list_risk_orders(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[RiskAssessmentRepository, Depends(get_risk_assessment_repo)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    models = await repo.list_by_store(store_id, limit=limit, offset=offset)
    items = [
        RiskOrderResponse(
            id=str(m.id),
            order_number=m.order_number,
            customer_name=m.customer_name,
            customer_email=m.customer_email,
            total_cents=m.total_cents,
            currency=m.currency,
            payment_method=m.payment_method,
            risk_score=m.risk_score,
            risk_level=m.risk_level,
            score_type=m.score_type,
            suggested_action=m.suggested_action,
            action_taken=m.action_taken,
            factors=m.factors or [],
            scored_at=m.scored_at,
            created_at=m.created_at,
        )
        for m in models
    ]
    return SuccessResponse(data=items)


@router.post(
    "/{store_id}/risk/orders/{order_id}/action",
    response_model=SuccessResponse[RiskOrderResponse],
    summary="Take action on a risky order",
    operation_id="shopify_risk_order_action",
)
async def take_risk_action(
    store_id: Annotated[UUID, Path()],
    order_id: Annotated[UUID, Path()],
    request: RiskActionRequest,
    repo: Annotated[RiskAssessmentRepository, Depends(get_risk_assessment_repo)],
):
    model = await repo.update_action(order_id, request.action)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Risk assessment not found",
        )

    return SuccessResponse(
        data=RiskOrderResponse(
            id=str(model.id),
            order_number=model.order_number,
            customer_name=model.customer_name,
            customer_email=model.customer_email,
            total_cents=model.total_cents,
            currency=model.currency,
            payment_method=model.payment_method,
            risk_score=model.risk_score,
            risk_level=model.risk_level,
            score_type=model.score_type,
            suggested_action=model.suggested_action,
            action_taken=model.action_taken,
            factors=model.factors or [],
            scored_at=model.scored_at,
            created_at=model.created_at,
        ),
        message=f"Action '{request.action}' recorded",
    )
