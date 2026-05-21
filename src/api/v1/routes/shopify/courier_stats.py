"""Courier statistics read endpoint (backend-023 / spec 013)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.shopify import verify_internal_key
from src.api.responses import SuccessResponse
from src.application.services.courier_stats_service import (
    RECOMMENDATION_MIN_SAMPLE,
)
from src.infrastructure.database.models.tenant.courier_stats import (
    CourierStatsModel,
)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


class CourierStatsRow(BaseModel):
    carrier: str
    period_start: date
    period_end: date
    total_shipments: int
    delivered_count: int
    returned_count: int
    failed_count: int
    in_progress_count: int
    cod_collected_count: int
    cod_total_count: int
    delivery_success_rate: float | None
    cod_collection_rate: float | None
    avg_delivery_hours: float | None
    last_refreshed_at: datetime


class CourierStatsResponse(BaseModel):
    rows: list[CourierStatsRow]
    recommended: list[CourierRecommendation]


class CourierRecommendation(BaseModel):
    carrier: str
    delivery_success_rate: float
    sample_size: int
    rank: Literal["best", "second", "third"]


@router.get(
    "/{store_id}/courier-stats",
    response_model=SuccessResponse[CourierStatsResponse],
    summary="List courier delivery statistics for a store",
    operation_id="shopify_list_courier_stats",
)
async def list_courier_stats(
    store_id: Annotated[UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_db)],
    period_start: Annotated[date | None, Query()] = None,
):
    query = select(CourierStatsModel).where(CourierStatsModel.store_id == store_id)
    if period_start is not None:
        query = query.where(CourierStatsModel.period_start == period_start)
    query = query.order_by(CourierStatsModel.period_start.desc())

    result = await session.execute(query)
    models = list(result.scalars().all())

    rows = [
        CourierStatsRow(
            carrier=m.carrier,
            period_start=m.period_start,
            period_end=m.period_end,
            total_shipments=m.total_shipments,
            delivered_count=m.delivered_count,
            returned_count=m.returned_count,
            failed_count=m.failed_count,
            in_progress_count=m.in_progress_count,
            cod_collected_count=m.cod_collected_count,
            cod_total_count=m.cod_total_count,
            delivery_success_rate=float(m.delivery_success_rate)
            if m.delivery_success_rate is not None
            else None,
            cod_collection_rate=float(m.cod_collection_rate)
            if m.cod_collection_rate is not None
            else None,
            avg_delivery_hours=float(m.avg_delivery_hours)
            if m.avg_delivery_hours is not None
            else None,
            last_refreshed_at=m.last_refreshed_at,
        )
        for m in models
    ]

    # Recommendations (spec 013 FR-002): ≥30-shipment minimum + ranked by
    # delivery_success_rate descending. Take the latest period only.
    latest_period = max((r.period_start for r in rows), default=None)
    eligible = [
        r
        for r in rows
        if r.period_start == latest_period
        and r.total_shipments >= RECOMMENDATION_MIN_SAMPLE
        and r.delivery_success_rate is not None
    ]
    eligible.sort(
        key=lambda r: r.delivery_success_rate or 0.0,
        reverse=True,
    )
    rank_labels: list[Literal["best", "second", "third"]] = [
        "best",
        "second",
        "third",
    ]
    recommended = [
        CourierRecommendation(
            carrier=r.carrier,
            delivery_success_rate=float(r.delivery_success_rate or 0.0),
            sample_size=r.total_shipments,
            rank=rank_labels[i],
        )
        for i, r in enumerate(eligible[:3])
    ]

    return SuccessResponse(
        data=CourierStatsResponse(rows=rows, recommended=recommended),
    )
