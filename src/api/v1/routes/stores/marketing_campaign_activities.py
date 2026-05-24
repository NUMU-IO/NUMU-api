"""Campaign activities — manual attribution backfill (feature 002 US5).

Endpoints under /api/v1/stores/{store_id}/marketing/campaigns/{campaign_id}/activities.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.responses import SuccessResponse
from src.application.services.audit_service import AuditService, EventType
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories.campaign_activity_repository import (
    CampaignActivityRepository,
)
from src.infrastructure.repositories.marketing_campaign_repository import (
    MarketingCampaignRepository,
)

router = APIRouter(
    prefix="/{store_id}/marketing/campaigns/{campaign_id}/activities",
    tags=["Marketing Campaign Activities"],
    dependencies=[Depends(verify_store_ownership)],
)

_MAX_WINDOW_DAYS = 365


# ── Schemas ──────────────────────────────────────────────────────


class BackfillFilterInput(BaseModel):
    field: Literal[
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "referrer",
    ]
    operator: Literal["equals", "starts_with", "contains"]
    value: str = Field(min_length=1, max_length=500)


class BackfillRequest(BaseModel):
    utm_filters: list[BackfillFilterInput] = Field(min_length=1, max_length=5)
    starts_at: datetime
    ends_at: datetime


class ActivityResponse(BaseModel):
    id: str
    type: str
    status: str
    payload: dict[str, Any]
    affected_count: int | None
    skipped_count: int | None
    error_message: str | None
    run_at: str
    completed_at: str | None
    run_by: str


# ── Helpers ──────────────────────────────────────────────────────


async def _load_campaign_or_404(store_id: UUID, campaign_id: UUID):
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        campaign = await repo.get_by_id(campaign_id)
        if campaign is None or campaign.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )
        return campaign


def _to_response(activity) -> ActivityResponse:
    return ActivityResponse(
        id=str(activity.id),
        type=activity.type,
        status=activity.status,
        payload=activity.payload,
        affected_count=activity.affected_count,
        skipped_count=activity.skipped_count,
        error_message=activity.error_message,
        run_at=activity.run_at.isoformat(),
        completed_at=activity.completed_at.isoformat()
        if activity.completed_at
        else None,
        run_by=str(activity.run_by),
    )


# ── Routes ───────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[list[ActivityResponse]],
    summary="List campaign activities (audit log)",
    operation_id="list_campaign_activities",
)
async def list_activities(
    store_id: UUID,
    campaign_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    type: str | None = Query(None),
):
    await _load_campaign_or_404(store_id, campaign_id)
    async with AsyncSessionLocal() as session:
        repo = CampaignActivityRepository(session)
        rows = await repo.list_for_campaign(campaign_id, limit=limit, type_=type)
    return SuccessResponse(
        data=[_to_response(r) for r in rows],
        message="Activities for campaign",
    )


@router.post(
    "/backfill",
    response_model=SuccessResponse[ActivityResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue an attribution backfill",
    operation_id="enqueue_campaign_backfill",
)
async def enqueue_backfill(
    store_id: UUID,
    campaign_id: UUID,
    body: BackfillRequest,
    user_id: UUID = Depends(get_current_user_id),
):
    campaign = await _load_campaign_or_404(store_id, campaign_id)

    if body.ends_at <= body.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ends_at must be > starts_at",
        )
    if body.ends_at - body.starts_at > timedelta(days=_MAX_WINDOW_DAYS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Window cannot exceed {_MAX_WINDOW_DAYS} days",
        )

    async with AsyncSessionLocal() as session:
        repo = CampaignActivityRepository(session)
        existing_running = await repo.get_running(campaign_id, "backfill_attribution")
        if existing_running is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A backfill is already running for this campaign",
            )

        payload = {
            "utm_filters": [
                {"field": f.field, "operator": f.operator, "value": f.value}
                for f in body.utm_filters
            ],
            "starts_at": body.starts_at,
            "ends_at": body.ends_at,
        }

        activity = await repo.create(
            tenant_id=campaign.tenant_id,
            store_id=store_id,
            campaign_id=campaign_id,
            type_="backfill_attribution",
            payload={
                **payload,
                "starts_at": body.starts_at.isoformat(),
                "ends_at": body.ends_at.isoformat(),
            },
            run_by=user_id,
        )

        # SEC-002b — audit log
        await AuditService(session).log(
            event_type=EventType.CAMPAIGN_BACKFILL_ENQUEUE,
            action="enqueue",
            resource_type="campaign_activity",
            resource_id=str(activity.id),
            user_id=user_id,
            store_id=store_id,
            tenant_id=campaign.tenant_id,
            new_value={
                "campaign_id": str(campaign_id),
                "filter_count": len(body.utm_filters),
                "window_days": (body.ends_at - body.starts_at).days,
            },
        )

        await session.commit()
        # Re-read so we have the row id + run_at populated
        activity_id = activity.id

    # Enqueue the Celery task. Import lazy to avoid pulling the worker
    # graph into request-time imports.
    from src.infrastructure.messaging.tasks.marketing_tasks import (
        backfill_campaign_attribution,
    )

    backfill_campaign_attribution.apply_async(
        kwargs={
            "activity_id": str(activity_id),
            "store_id": str(store_id),
            "campaign_id": str(campaign_id),
            "payload": {
                "utm_filters": payload["utm_filters"],
                "starts_at": body.starts_at,
                "ends_at": body.ends_at,
            },
        },
        queue="default",
    )

    return SuccessResponse(
        data=_to_response(activity),
        message="Backfill queued",
    )
