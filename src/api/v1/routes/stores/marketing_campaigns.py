"""Merchant marketing campaign routes — Phase 8.6.

Mounted at /stores/{store_id}/marketing/campaigns/

Endpoints:
  GET    /                    — list (optional ?status= ?channel=)
  POST   /                    — create DRAFT
  GET    /{id}                — single
  PUT    /{id}                — edit (DRAFT only)
  POST   /{id}/schedule       — DRAFT → SCHEDULED with scheduled_at
  POST   /{id}/send-now       — DRAFT/SCHEDULED → SENDING (immediate)
  POST   /{id}/cancel         — non-terminal → CANCELED

The Celery beat task `marketing.campaign.process_scheduled` picks up
SCHEDULED campaigns whose scheduled_at <= now() and dispatches them
via the channel-specific service (Twilio for SMS, Resend for email).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.core.entities.marketing_campaign import (
    CampaignChannel,
    CampaignStatus,
    MarketingCampaign,
)
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories import StoreRepository
from src.infrastructure.repositories.marketing_campaign_repository import (
    MarketingCampaignRepository,
)

router = APIRouter(
    prefix="/{store_id}/marketing/campaigns",
    tags=["Marketing Campaigns"],
    dependencies=[Depends(verify_store_ownership)],
)


# ── Schemas ──────────────────────────────────────────────────────


class CreateCampaignRequest(BaseModel):
    channel: CampaignChannel
    name: str = Field(min_length=1, max_length=255)
    template_id: UUID | None = None
    inline_subject: str | None = Field(None, max_length=255)
    inline_body: str | None = Field(None, max_length=10_000)
    segment_id: UUID | None = None
    audience_filter: dict | None = None
    scheduled_at: datetime | None = None
    note: str | None = Field(None, max_length=2_000)


class UpdateCampaignRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    template_id: UUID | None = None
    inline_subject: str | None = None
    inline_body: str | None = None
    segment_id: UUID | None = None
    audience_filter: dict | None = None
    note: str | None = None


class ScheduleRequest(BaseModel):
    scheduled_at: datetime


class CampaignResponse(BaseModel):
    id: str
    channel: str
    name: str
    status: str
    template_id: str | None = None
    inline_subject: str | None = None
    inline_body: str | None = None
    segment_id: str | None = None
    audience_filter: dict | None = None
    scheduled_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    canceled_at: str | None = None
    total_recipients: int
    sent_count: int
    delivered_count: int
    failed_count: int
    note: str | None = None
    created_at: str
    updated_at: str


def _to_response(c: MarketingCampaign) -> CampaignResponse:
    return CampaignResponse(
        id=str(c.id),
        channel=c.channel.value,
        name=c.name,
        status=c.status.value,
        template_id=str(c.template_id) if c.template_id else None,
        inline_subject=c.inline_subject,
        inline_body=c.inline_body,
        segment_id=str(c.segment_id) if c.segment_id else None,
        audience_filter=c.audience_filter,
        scheduled_at=c.scheduled_at.isoformat() if c.scheduled_at else None,
        started_at=c.started_at.isoformat() if c.started_at else None,
        completed_at=c.completed_at.isoformat() if c.completed_at else None,
        canceled_at=c.canceled_at.isoformat() if c.canceled_at else None,
        total_recipients=c.total_recipients,
        sent_count=c.sent_count,
        delivered_count=c.delivered_count,
        failed_count=c.failed_count,
        note=c.note,
        created_at=c.created_at.isoformat() if c.created_at else "",
        updated_at=c.updated_at.isoformat() if c.updated_at else "",
    )


# ── Routes ───────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[list[CampaignResponse]],
    summary="List campaigns",
    operation_id="list_marketing_campaigns",
)
async def list_campaigns(
    store_id: UUID,
    status_filter: CampaignStatus | None = Query(None, alias="status"),
    channel: CampaignChannel | None = Query(None),
):
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        rows = await repo.list_for_store(
            store_id, status=status_filter, channel=channel
        )
    return SuccessResponse(
        data=[_to_response(c) for c in rows], message="Campaigns listed"
    )


@router.post(
    "",
    response_model=SuccessResponse[CampaignResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft campaign",
    operation_id="create_marketing_campaign",
)
async def create_campaign(
    store_id: UUID,
    body: CreateCampaignRequest,
    user_id: UUID = Depends(get_current_user_id),
    store_repo: StoreRepository = Depends(get_store_repository),
):
    if body.template_id is None and not body.inline_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either template_id or inline_body must be provided.",
        )
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    initial_status = (
        CampaignStatus.SCHEDULED
        if body.scheduled_at is not None
        else CampaignStatus.DRAFT
    )

    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        created = await repo.create(
            MarketingCampaign(
                tenant_id=store.tenant_id,
                store_id=store_id,
                channel=body.channel,
                name=body.name,
                status=initial_status,
                template_id=body.template_id,
                inline_subject=body.inline_subject,
                inline_body=body.inline_body,
                segment_id=body.segment_id,
                audience_filter=body.audience_filter,
                scheduled_at=body.scheduled_at,
                note=body.note,
                created_by=user_id,
            )
        )
        await session.commit()
    return SuccessResponse(data=_to_response(created), message="Campaign created")


@router.get(
    "/{campaign_id}",
    response_model=SuccessResponse[CampaignResponse],
    summary="Get campaign",
    operation_id="get_marketing_campaign",
)
async def get_campaign(store_id: UUID, campaign_id: UUID):
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        c = await repo.get_by_id(campaign_id)
    if c is None or c.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
        )
    return SuccessResponse(data=_to_response(c), message="Campaign retrieved")


@router.put(
    "/{campaign_id}",
    response_model=SuccessResponse[CampaignResponse],
    summary="Edit a draft campaign",
    operation_id="update_marketing_campaign",
)
async def update_campaign(
    store_id: UUID, campaign_id: UUID, body: UpdateCampaignRequest
):
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        c = await repo.get_by_id(campaign_id)
        if c is None or c.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
            )
        if c.status != CampaignStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot edit a {c.status.value} campaign.",
            )
        # Direct field updates on the model — simpler than a service
        # layer for one in-place edit.
        from sqlalchemy import select as _select

        from src.infrastructure.database.models.tenant.marketing_campaign import (
            MarketingCampaignModel,
        )

        row = (
            await session.execute(
                _select(MarketingCampaignModel).where(
                    MarketingCampaignModel.id == campaign_id
                )
            )
        ).scalar_one()
        if body.name is not None:
            row.name = body.name
        if body.template_id is not None:
            row.template_id = body.template_id
        if body.inline_subject is not None:
            row.inline_subject = body.inline_subject
        if body.inline_body is not None:
            row.inline_body = body.inline_body
        if body.segment_id is not None:
            row.segment_id = body.segment_id
        if body.audience_filter is not None:
            row.audience_filter = body.audience_filter
        if body.note is not None:
            row.note = body.note
        await session.flush()
        updated = await repo.get_by_id(campaign_id)
        await session.commit()
    return SuccessResponse(data=_to_response(updated), message="Campaign updated")


@router.post(
    "/{campaign_id}/schedule",
    response_model=SuccessResponse[CampaignResponse],
    summary="Schedule a campaign send",
    operation_id="schedule_marketing_campaign",
)
async def schedule_campaign(
    store_id: UUID, campaign_id: UUID, body: ScheduleRequest
):
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        c = await repo.get_by_id(campaign_id)
        if c is None or c.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
            )
        if not c.can_transition_to(CampaignStatus.SCHEDULED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot schedule a {c.status.value} campaign.",
            )
        updated = await repo.transition(
            campaign_id, CampaignStatus.SCHEDULED, scheduled_at=body.scheduled_at
        )
        await session.commit()
    return SuccessResponse(
        data=_to_response(updated),
        message=f"Campaign scheduled for {body.scheduled_at.isoformat()}",
    )


@router.post(
    "/{campaign_id}/send-now",
    response_model=SuccessResponse[CampaignResponse],
    summary="Trigger immediate send",
    operation_id="send_marketing_campaign_now",
)
async def send_now(store_id: UUID, campaign_id: UUID):
    """Skip the scheduler — push the campaign straight to SENDING.
    The next Celery sweep tick picks it up; in dev / test the runner
    can be invoked directly via the management script."""
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        c = await repo.get_by_id(campaign_id)
        if c is None or c.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
            )
        if not c.can_transition_to(CampaignStatus.SENDING):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot send a {c.status.value} campaign.",
            )
        updated = await repo.transition(campaign_id, CampaignStatus.SENDING)
        await session.commit()
    return SuccessResponse(
        data=_to_response(updated), message="Campaign queued for immediate send"
    )


@router.post(
    "/{campaign_id}/cancel",
    response_model=SuccessResponse[CampaignResponse],
    summary="Cancel a campaign",
    operation_id="cancel_marketing_campaign",
)
async def cancel_campaign(store_id: UUID, campaign_id: UUID):
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        c = await repo.get_by_id(campaign_id)
        if c is None or c.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
            )
        if not c.can_transition_to(CampaignStatus.CANCELED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel a {c.status.value} campaign.",
            )
        updated = await repo.transition(campaign_id, CampaignStatus.CANCELED)
        await session.commit()
    return SuccessResponse(
        data=_to_response(updated), message="Campaign canceled"
    )
