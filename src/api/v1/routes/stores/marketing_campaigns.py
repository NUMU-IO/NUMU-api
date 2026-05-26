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

import base64
import io
from datetime import datetime
from typing import Literal
from uuid import UUID

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.dependencies.repositories import (
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.application.services import campaign_coupon_service, short_link_service
from src.application.services.audit_service import AuditService, EventType
from src.application.services.link_builder import LinkBuilder
from src.application.services.short_code_generator import (
    generate as generate_short_code,
)
from src.config.logging_config import get_logger
from src.core.entities.coupon import Coupon, CouponType
from src.core.entities.marketing_campaign import (
    CampaignChannel,
    CampaignStatus,
    MarketingCampaign,
)
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories import ProductRepository, StoreRepository
from src.infrastructure.repositories.analytics_repository import (
    AnalyticsRepository,
)
from src.infrastructure.repositories.coupon_repository import CouponRepository
from src.infrastructure.repositories.marketing_campaign_repository import (
    MarketingCampaignRepository,
)

logger = get_logger(__name__)

router = APIRouter(
    prefix="/{store_id}/marketing/campaigns",
    tags=["Marketing Campaigns"],
    dependencies=[Depends(verify_store_ownership)],
)


# ── Schemas ──────────────────────────────────────────────────────


class _EstimateAudienceRequest(BaseModel):
    """Body for ``POST /marketing/campaigns/audience/estimate``.

    Mirrors the channel + audience_filter pair of a create request so
    the hub can ping the estimate route with the same shape it'll
    eventually POST as a draft. Channel matters because EMAIL filters
    on email-not-null while SMS filters on phone-not-null.
    """

    channel: CampaignChannel
    audience_filter: dict | None = None


class _AudienceEstimateResponse(BaseModel):
    """Audience count + sample list for the pre-send preview panel."""

    estimated_count: int
    sample: list[dict] = Field(default_factory=list)


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
    # See MarketingCampaign.promoted_item docstring for shape. Stored
    # verbatim — the hub's PromotedItemPicker fills the snapshot from its
    # existing product list so we don't re-query here. NULL means "this
    # campaign isn't promoting a specific item" (just a freeform message).
    promoted_item: dict | None = None


class UpdateCampaignRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    template_id: UUID | None = None
    inline_subject: str | None = None
    inline_body: str | None = None
    segment_id: UUID | None = None
    audience_filter: dict | None = None
    note: str | None = None
    promoted_item: dict | None = None


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
    promoted_item: dict | None = None
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
        promoted_item=c.promoted_item,
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
    "/audience/estimate",
    response_model=SuccessResponse[_AudienceEstimateResponse],
    summary="Estimate the audience size for a draft campaign filter",
    operation_id="estimate_marketing_audience",
)
async def estimate_audience_route(
    store_id: UUID,
    body: _EstimateAudienceRequest,
):
    """Resolve a filter into an audience count + sample for the hub's
    pre-send preview panel.

    Stateless — does NOT mutate the campaign row. The hub calls this
    every time the merchant tweaks a filter field so they see a live
    "Will send to ~N recipients" count before clicking Save / Send.
    """
    from src.application.services.marketing_audience_resolver import (
        MarketingAudienceFilter,
        estimate_audience,
    )

    try:
        filt = MarketingAudienceFilter.model_validate(body.audience_filter or {})
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid audience_filter: {exc}",
        ) from exc

    async with AsyncSessionLocal() as session:
        estimate = await estimate_audience(
            session,
            store_id=store_id,
            filter_in=filt,
            channel=body.channel,
        )
    return SuccessResponse(
        data=_AudienceEstimateResponse(
            estimated_count=estimate.estimated_count,
            sample=[
                {"id": str(s.id), "name": s.name, "contact": s.contact}
                for s in estimate.sample
            ],
        ),
        message="Audience estimated",
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
        # Stable trackable-link identifier — generated once, never
        # regenerated on rename. See research.md R-02.
        short_code = await generate_short_code(store_id, session)
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
                promoted_item=body.promoted_item,
                created_by=user_id,
                short_code=short_code,
            )
        )
        # SEC-008: audit-log the create so disputes over attribution
        # ("who created this campaign?") have a definitive trail.
        await AuditService(session).log(
            event_type=EventType.CAMPAIGN_CREATE,
            action="create",
            resource_type="marketing_campaign",
            resource_id=str(created.id),
            user_id=user_id,
            store_id=store_id,
            tenant_id=store.tenant_id,
            new_value={
                "name": created.name,
                "channel": created.channel.value,
                "short_code": created.short_code,
                "status": created.status.value,
            },
        )
        await session.commit()
    return SuccessResponse(data=_to_response(created), message="Campaign created")


# ── Cross-campaign comparison (feature 002 US7) ──────────────────
#
# MUST be declared BEFORE the dynamic /{campaign_id} route so FastAPI
# matches the static path first; otherwise "compare" would be parsed
# as a UUID and 422 out.


class CompareKpis(BaseModel):
    sessions: int
    sales_cents: int
    orders: int
    average_order_value_cents: int


class CompareSeriesPoint(BaseModel):
    date: str
    sessions: int
    sales_cents: int


class CompareCampaignBlock(BaseModel):
    id: str
    name: str | None
    short_code: str | None
    status: str | None
    found: bool
    kpis: CompareKpis | None
    series: list[CompareSeriesPoint]


class CompareWarning(BaseModel):
    code: str
    message: str


class CompareResponse(BaseModel):
    date_from: str
    date_to: str
    attribution_model: str
    granularity: str
    campaigns: list[CompareCampaignBlock]
    warnings: list[CompareWarning] = []


@router.get(
    "/compare",
    response_model=SuccessResponse[CompareResponse],
    summary="Compare 2-4 campaigns side-by-side",
    operation_id="compare_marketing_campaigns",
)
async def compare_campaigns(
    store_id: UUID,
    ids: str = Query(..., description="Comma-separated campaign UUIDs (2-4)"),
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    attribution_model: AttributionModel = Query("last_touch"),
    granularity: Literal["day", "week"] | None = Query(None),
):
    """Side-by-side comparison of 2-4 campaigns.

    SEC-001: every requested id is pre-filtered against the path
    store_id BEFORE any data is read. IDs from a different tenant are
    returned with ``found: false`` + a warning — NEVER a stub with a
    name/short_code that would confirm their existence in another
    tenant.
    """
    _validate_window(date_from, date_to)

    try:
        requested_ids = [UUID(s.strip()) for s in ids.split(",") if s.strip()]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID in ids: {exc!s}",
        ) from exc

    if len(requested_ids) < 2 or len(requested_ids) > 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pick 2 to 4 campaigns",
        )

    # Pick granularity: day for windows < 60 days, else week. Caller
    # can override via the query param.
    chosen_granularity = granularity or (
        "day" if (date_to - date_from).days < 60 else "week"
    )

    # SEC-001 pre-filter: load only the campaigns that actually belong
    # to this store. Unknown / cross-tenant ids drop here.
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        loaded_by_id: dict[UUID, object] = {}
        for cid in requested_ids:
            c = await repo.get_by_id(cid)
            if c is not None and c.store_id == store_id:
                loaded_by_id[cid] = c

        found_ids = list(loaded_by_id.keys())

        if not found_ids:
            return SuccessResponse(
                data=CompareResponse(
                    date_from=date_from.isoformat(),
                    date_to=date_to.isoformat(),
                    attribution_model=attribution_model,
                    granularity=chosen_granularity,
                    campaigns=[
                        CompareCampaignBlock(
                            id=str(cid),
                            name=None,
                            short_code=None,
                            status=None,
                            found=False,
                            kpis=None,
                            series=[],
                        )
                        for cid in requested_ids
                    ],
                    warnings=[
                        CompareWarning(
                            code="campaign_unavailable",
                            message=(
                                f"{len(requested_ids)} of {len(requested_ids)} "
                                "campaigns are no longer available"
                            ),
                        )
                    ],
                ),
                message="Comparison ready",
            )

        analytics = AnalyticsRepository(session)
        compare = await analytics.campaign_compare(
            store_id=store_id,
            campaign_ids=found_ids,
            date_from=date_from,
            date_to=date_to,
            granularity=chosen_granularity,
        )

    blocks: list[CompareCampaignBlock] = []
    for cid in requested_ids:
        if cid not in loaded_by_id:
            blocks.append(
                CompareCampaignBlock(
                    id=str(cid),
                    name=None,
                    short_code=None,
                    status=None,
                    found=False,
                    kpis=None,
                    series=[],
                )
            )
            continue
        c = loaded_by_id[cid]
        k = compare["kpis"].get(
            cid,
            {
                "sessions": 0,
                "orders": 0,
                "sales_cents": 0,
                "average_order_value_cents": 0,
            },
        )
        s = compare["series_by_campaign"].get(cid, [])
        blocks.append(
            CompareCampaignBlock(
                id=str(cid),
                name=c.name,
                short_code=c.short_code,
                status=c.status.value if hasattr(c.status, "value") else str(c.status),
                found=True,
                kpis=CompareKpis(**k),
                series=[CompareSeriesPoint(**pt) for pt in s],
            )
        )

    missing = len(requested_ids) - len(found_ids)
    warnings: list[CompareWarning] = []
    if missing > 0:
        warnings.append(
            CompareWarning(
                code="campaign_unavailable",
                message=(
                    f"{missing} of {len(requested_ids)} campaigns "
                    "are no longer available"
                ),
            )
        )

    return SuccessResponse(
        data=CompareResponse(
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            attribution_model=attribution_model,
            granularity=chosen_granularity,
            campaigns=blocks,
            warnings=warnings,
        ),
        message="Comparison ready",
    )


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
        if body.promoted_item is not None:
            row.promoted_item = body.promoted_item
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
async def schedule_campaign(store_id: UUID, campaign_id: UUID, body: ScheduleRequest):
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
    """Skip the scheduler — push the campaign straight to SENDING and
    enqueue the dispatch task directly.

    The periodic sweep is the backstop — if this enqueue fails (network
    blip between the SENDING transition and apply_async), the sweep
    rescues the orphan within ~5 minutes (see
    ``_ORPHAN_SENDING_AGE`` in marketing_campaign_tasks.py).
    """
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

    # Lazy import — keep the request hot path lean and avoid a
    # circular import via the celery worker module graph.
    from src.infrastructure.messaging.tasks.marketing_campaign_tasks import (
        dispatch_marketing_campaign,
    )

    dispatch_marketing_campaign.apply_async(
        kwargs={"campaign_id": str(campaign_id)},
        queue="messaging",
    )

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
    return SuccessResponse(data=_to_response(updated), message="Campaign canceled")


@router.post(
    "/{campaign_id}/duplicate",
    response_model=SuccessResponse[CampaignResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate a campaign as a new Draft",
    operation_id="duplicate_marketing_campaign",
)
async def duplicate_campaign(
    store_id: UUID,
    campaign_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
):
    """One-click duplicate (feature 002 US6).

    Copies: name (suffixed " (Copy)"), channel, inline_subject,
    inline_body, template_id, audience_filter, segment_id, note.
    NOT copied: trackable links, auto-match rules, campaign activities,
    coupons, status fields (always Draft), counters. Mints a fresh
    short_code per FR-030.

    SEC-002c: audit-log the duplicate so disputes ("who copied this
    campaign?") have a definitive trail.
    """
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        source = await repo.get_by_id(campaign_id)
        if source is None or source.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        # Fresh short_code generated against this store so the URL space
        # stays unambiguous; the source's short_code MUST NOT be reused
        # (uq_campaigns_store_short_code would reject anyway).
        new_short_code = await generate_short_code(store_id, session)
        new_name = f"{source.name} (Copy)"
        # Truncate name if (Copy) suffix would exceed the model's 255-char cap.
        if len(new_name) > 255:
            new_name = new_name[:255]

        created = await repo.create(
            MarketingCampaign(
                tenant_id=source.tenant_id,
                store_id=store_id,
                channel=source.channel,
                name=new_name,
                status=CampaignStatus.DRAFT,
                template_id=source.template_id,
                inline_subject=source.inline_subject,
                inline_body=source.inline_body,
                segment_id=source.segment_id,
                audience_filter=source.audience_filter,
                scheduled_at=None,
                note=source.note,
                promoted_item=source.promoted_item,
                created_by=user_id,
                short_code=new_short_code,
            )
        )

        await AuditService(session).log(
            event_type=EventType.CAMPAIGN_DUPLICATE,
            action="duplicate",
            resource_type="marketing_campaign",
            resource_id=str(created.id),
            user_id=user_id,
            store_id=store_id,
            tenant_id=source.tenant_id,
            new_value={
                "source_campaign_id": str(campaign_id),
                "new_campaign_id": str(created.id),
                "channel": created.channel.value,
            },
        )
        await session.commit()

    return SuccessResponse(data=_to_response(created), message="Campaign duplicated")


# ── Trackable-link generator (feature 001) ───────────────────────


_VALID_SOURCES = {
    "facebook",
    "instagram",
    "whatsapp",
    "email",
    "tiktok",
    "sms",
    "qr",
    "other",
}


class TrackableLinkDestination(BaseModel):
    """Where the trackable link should resolve to.

    For ``custom`` destinations, the caller is responsible for having
    pre-validated the path via POST ``/storefront/validate-path``. This
    endpoint composes the URL only — it does not re-validate.
    """

    kind: Literal["homepage", "collection", "product", "custom"]
    collection_slug: str | None = Field(default=None, max_length=200)
    product_id: UUID | None = None
    custom_path: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _enforce_kind_specific_field(self) -> TrackableLinkDestination:
        if self.kind == "collection" and not self.collection_slug:
            raise ValueError("collection_slug is required when kind=collection")
        if self.kind == "product" and self.product_id is None:
            raise ValueError("product_id is required when kind=product")
        if self.kind == "custom" and not self.custom_path:
            raise ValueError("custom_path is required when kind=custom")
        return self


class TrackableLinkRequest(BaseModel):
    destination: TrackableLinkDestination
    source: str = Field(min_length=1, max_length=40)
    medium: str | None = Field(default=None, max_length=40)
    term: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200)
    # Opt-in to also creating a /r/{code} short link. Defaults to
    # False so existing callers keep their current behaviour; the
    # merchant hub flips it to True when the merchant clicks the
    # "Copy short link" affordance.
    with_short_link: bool = False


class TrackableLinkDestinationResponse(BaseModel):
    kind: str
    product_id: str | None = None
    collection_slug: str | None = None
    resolved_path: str


class TrackableLinkResponse(BaseModel):
    url: str
    qr_png_base64: str
    short_code: str
    campaign_slug: str
    destination: TrackableLinkDestinationResponse
    # Optional short URL — only set when the request had
    # ``with_short_link=true``. Shape: ``https://<store-host>/r/{code}``
    # (uses the store's canonical origin, not the apex, so the
    # displayed link looks branded).
    # The 8-char ``short_url_code`` is the per-link identifier (not the
    # 6-char campaign short_code above); they live in different
    # namespaces.
    short_url: str | None = None
    short_url_code: str | None = None


def _render_qr_png_base64(url: str) -> str:
    """Render the URL as a 512×512 PNG, error-correction level M, base64."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@router.post(
    "/{campaign_id}/trackable-link",
    response_model=SuccessResponse[TrackableLinkResponse],
    summary="Generate a trackable URL + QR for a campaign destination",
    operation_id="generate_trackable_link",
)
async def generate_trackable_link(
    store_id: UUID,
    campaign_id: UUID,
    body: TrackableLinkRequest,
    user_id: UUID = Depends(get_current_user_id),
    store_repo: StoreRepository = Depends(get_store_repository),
    product_repo: ProductRepository = Depends(get_product_repository),
):
    if body.source not in _VALID_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown source preset: {body.source!r}",
        )

    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    # SEC-001: load the campaign filtering by BOTH (id, store_id). 404
    # (not 403) so cross-tenant probes can't distinguish "exists but
    # not yours" from "doesn't exist".
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        campaign = await repo.get_by_id(campaign_id)
    if campaign is None or campaign.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
        )

    builder = LinkBuilder(store)
    destination = body.destination
    destination_resp: TrackableLinkDestinationResponse

    if destination.kind == "homepage":
        url = builder.storefront_url(
            campaign=campaign,
            source=body.source,
            medium=body.medium,
            term=body.term,
            content=body.content,
        )
        destination_resp = TrackableLinkDestinationResponse(
            kind="homepage", resolved_path="/"
        )
    elif destination.kind == "collection":
        url = builder.collection_url(
            collection_slug=destination.collection_slug or "",
            campaign=campaign,
            source=body.source,
            medium=body.medium,
            term=body.term,
            content=body.content,
        )
        destination_resp = TrackableLinkDestinationResponse(
            kind="collection",
            collection_slug=destination.collection_slug,
            resolved_path=f"/collections?category={destination.collection_slug}",
        )
    elif destination.kind == "product":
        product = await product_repo.get_by_id(destination.product_id)
        if product is None or product.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Product not found on this store",
            )
        url = builder.product_url(
            product=product,
            campaign=campaign,
            source=body.source,
            medium=body.medium,
            term=body.term,
            content=body.content,
        )
        slug_or_id = (product.slug or "").strip() or str(product.id)
        destination_resp = TrackableLinkDestinationResponse(
            kind="product",
            product_id=str(product.id),
            resolved_path=f"/product/{slug_or_id}",
        )
    else:  # custom
        url = builder.custom_url(
            path=destination.custom_path or "",
            campaign=campaign,
            source=body.source,
            medium=body.medium,
            term=body.term,
            content=body.content,
        )
        destination_resp = TrackableLinkDestinationResponse(
            kind="custom", resolved_path=destination.custom_path
        )

    qr_png_b64 = _render_qr_png_base64(url)

    # Optional short-link mint. Same session is used for the row +
    # the audit log so a failed validation rolls everything back. The
    # short_url is composed against the apex storefront base domain
    # (numueg.app) — independent of the store's subdomain or custom
    # domain because the redirector lives at the root host.
    short_url: str | None = None
    short_url_code: str | None = None
    if body.with_short_link:
        async with AsyncSessionLocal() as link_session:
            try:
                short_link_row = await short_link_service.create_short_link(
                    session=link_session,
                    store=store,
                    destination_url=url,
                    campaign_id=campaign_id,
                    created_by=user_id,
                )
                await link_session.commit()
                short_url_code = short_link_row.short_code
                # Use the store's canonical origin (subdomain or
                # custom_domain) so the displayed short link looks
                # branded — e.g. `https://yreab-test.numueg.app/r/AB7K9XYZ`
                # instead of the apex `numueg.app/r/...`. The /r/{code}
                # route is still handled by the API (test env routes
                # via nginx hostname split). Storefronts without an
                # `/r/...` proxy fall back via Cloudflare; for prod
                # Heroku see follow-up task to add a Next.js route.
                short_url = f"{store.store_url.rstrip('/')}/r/{short_url_code}"
            except short_link_service.OpenRedirectorError:
                # The destination didn't pass the host check — should
                # never happen because the link builder composed it
                # against this store's canonical origin, but guard
                # anyway: fail the whole endpoint rather than returning
                # a long URL silently sans short URL, which would mask
                # a real bug.
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="short link could not be created for this destination",
                )
            except short_link_service.ShortLinkCreationError:
                # Generator exhausted its collision-retry budget. At
                # 32^8 ≈ 1.1T short_codes this is astronomically
                # unlikely in normal use (would imply a broken RNG or
                # a trillion-row table). Surface as 503 — transient
                # so the merchant can simply retry the request — and
                # with an explicit detail so it doesn't get confused
                # with the OpenRedirectorError path above.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "short link generator could not find a free code; "
                        "retry the request"
                    ),
                )

    # SEC-008: audit-log the link generation. The URL itself is what
    # gets shared externally — having a who/when trail per link
    # answers attribution disputes after the fact. We open a small
    # session just for this write since the rest of the request path
    # is read-only.
    async with AsyncSessionLocal() as audit_session:
        await AuditService(audit_session).log(
            event_type=EventType.CAMPAIGN_TRACKABLE_LINK_GENERATE,
            action="generate",
            resource_type="marketing_campaign",
            resource_id=str(campaign_id),
            user_id=user_id,
            store_id=store_id,
            tenant_id=campaign.tenant_id,
            details={
                "destination_kind": destination.kind,
                "source": body.source,
                "medium": body.medium,
                "url": url,
                "short_url_code": short_url_code,
            },
        )
        await audit_session.commit()

    return SuccessResponse(
        data=TrackableLinkResponse(
            url=url,
            qr_png_base64=qr_png_b64,
            short_code=campaign.short_code,
            campaign_slug=LinkBuilder.slug_from_campaign_name(campaign.name),
            destination=destination_resp,
            short_url=short_url,
            short_url_code=short_url_code,
        ),
        message="Trackable link generated",
    )


# ── Per-campaign performance (feature 001 US3) ──────────────────


class ConversionRatesResponse(BaseModel):
    session_to_atc: float
    atc_to_checkout: float
    checkout_to_order: float
    session_to_order: float


class TopProductResponse(BaseModel):
    product_id: str | None = None
    name: str | None = None
    orders: int
    revenue_cents: int


class CampaignPerformanceResponse(BaseModel):
    campaign_id: str
    campaign_name: str
    short_code: str
    date_from: str
    date_to: str
    totals: CampaignPerformanceTotals


class CouponRedemptionBreakdownItem(BaseModel):
    """One redeemed coupon code within a campaign window."""

    code: str
    redemptions: int
    discount_value_cents: int
    revenue_cents: int
    # True when the code was minted via POST /campaigns/{id}/coupons
    # (i.e. tied to the campaign at creation). False for codes the
    # customer pasted that happened to also attribute to this campaign
    # via UTM resolution — still counted, but flagged differently in
    # the hub so merchants can tell organic from intended redemptions.
    campaign_issued: bool


class CampaignPerformanceTotals(BaseModel):
    sessions: int
    product_views: int
    add_to_cart: int
    checkout_started: int
    orders: int
    revenue_cents: int
    average_order_value_cents: int
    conversion_rates: ConversionRatesResponse
    top_products: list[TopProductResponse]
    # Post-feature-001: total redemptions of any coupon attached to
    # this campaign, plus a per-code breakdown for the dashboard.
    coupon_redemptions: int = 0
    coupon_discount_value_cents: int = 0
    coupon_breakdown: list[CouponRedemptionBreakdownItem] = Field(default_factory=list)


CampaignPerformanceResponse.model_rebuild()


@router.get(
    "/{campaign_id}/performance",
    response_model=SuccessResponse[CampaignPerformanceResponse],
    summary="Per-campaign performance rollup",
    operation_id="get_campaign_performance",
)
async def get_campaign_performance(
    store_id: UUID,
    campaign_id: UUID,
    date_from: datetime = Query(..., description="Range start (inclusive)"),
    date_to: datetime = Query(..., description="Range end (inclusive)"),
):
    """Sessions, ATCs, checkouts, orders, revenue, AOV, conversion rates,
    top products for a campaign over a date range.

    SEC-001: load the campaign WHERE id = :campaign_id AND store_id =
    :store_id; 404 on mismatch (never 403 — avoids leaking campaign
    existence across tenants). The analytics-repo method is also
    scoped by (store_id, campaign_id) at the SQL level, so a probe
    that somehow slipped past the route check cannot leak via the
    underlying query either.
    """
    if date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date_to must be >= date_from",
        )

    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        campaign = await repo.get_by_id(campaign_id)
        if campaign is None or campaign.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )
        analytics = AnalyticsRepository(session)
        totals = await analytics.campaign_performance(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )

    return SuccessResponse(
        data=CampaignPerformanceResponse(
            campaign_id=str(campaign_id),
            campaign_name=campaign.name,
            short_code=campaign.short_code,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            totals=CampaignPerformanceTotals(
                sessions=totals["sessions"],
                product_views=totals["product_views"],
                add_to_cart=totals["add_to_cart"],
                checkout_started=totals["checkout_started"],
                orders=totals["orders"],
                revenue_cents=totals["revenue_cents"],
                average_order_value_cents=totals["average_order_value_cents"],
                conversion_rates=ConversionRatesResponse(**totals["conversion_rates"]),
                top_products=[TopProductResponse(**p) for p in totals["top_products"]],
                coupon_redemptions=totals.get("coupon_redemptions", 0),
                coupon_discount_value_cents=totals.get(
                    "coupon_discount_value_cents", 0
                ),
                coupon_breakdown=[
                    CouponRedemptionBreakdownItem(**c)
                    for c in totals.get("coupon_breakdown", [])
                ],
            ),
        ),
        message="Campaign performance retrieved",
    )


# ── Per-campaign breakdowns (feature 002 US3) ─────────────────────
#
# Five GET endpoints feeding the Shopify-style chart grid on the
# campaign detail page. Each accepts ?date_from&date_to and is
# (store_id, campaign_id, tenant)-scoped. The 365-day window cap
# matches FR-028's backfill cap (consistent operator guardrail).
# attribution_model query param is accepted for forward-compat with
# US3's model pill but not yet consumed — breakdown semantics here are
# stable across the model selector; only multi-touch credits change.


_MAX_WINDOW_DAYS = 365


async def _load_campaign_or_404(store_id: UUID, campaign_id: UUID):
    """Common (store_id, campaign_id) tenant-scoped lookup.

    Returns the campaign or raises 404. 404 (not 403) is the right
    error per the existing SEC-001 convention — it avoids leaking
    cross-tenant campaign existence.
    """
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        campaign = await repo.get_by_id(campaign_id)
        if campaign is None or campaign.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )
        return campaign


def _validate_window(date_from: datetime, date_to: datetime) -> None:
    if date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date_to must be >= date_from",
        )
    if (date_to - date_from).days > _MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date window cannot exceed {_MAX_WINDOW_DAYS} days",
        )


# Response models — flat shapes per contracts/analytics-breakdowns.md.
# Each wraps a dict, with a Channel/Combo/Bin/Device row type.


class _ChannelRow(BaseModel):
    channel: str
    sessions: int
    sales_cents: int


class CampaignBreakdownChannelResponse(BaseModel):
    campaign_id: str
    date_from: str
    date_to: str
    attribution_model: str
    channels: list[_ChannelRow]


class _UtmComboRow(BaseModel):
    utm_source: str | None
    utm_medium: str | None
    utm_campaign: str | None
    utm_term: str | None
    utm_content: str | None
    sessions: int
    sales_cents: int


class CampaignBreakdownUtmResponse(BaseModel):
    campaign_id: str
    date_from: str
    date_to: str
    attribution_model: str
    combos: list[_UtmComboRow]


class _CustomerTypeBlock(BaseModel):
    orders: int
    sales_cents: int


class CampaignBreakdownCustomerTypeResponse(BaseModel):
    campaign_id: str
    date_from: str
    date_to: str
    attribution_model: str
    new_customers: _CustomerTypeBlock
    returning_customers: _CustomerTypeBlock


class _OrderSizeBin(BaseModel):
    lower_cents: int
    upper_cents: int | None
    orders: int


class CampaignBreakdownOrderSizeResponse(BaseModel):
    campaign_id: str
    date_from: str
    date_to: str
    attribution_model: str
    bins: list[_OrderSizeBin]


class _DeviceRow(BaseModel):
    device: str
    sessions: int


class CampaignBreakdownDeviceResponse(BaseModel):
    campaign_id: str
    date_from: str
    date_to: str
    attribution_model: str
    devices: list[_DeviceRow]


AttributionModel = Literal[
    "last_touch", "first_touch", "linear", "time_decay", "position_based"
]


@router.get(
    "/{campaign_id}/breakdown/channel",
    response_model=SuccessResponse[CampaignBreakdownChannelResponse],
    summary="Sessions + sales by channel for a campaign",
    operation_id="get_campaign_breakdown_channel",
)
async def get_campaign_breakdown_channel(
    store_id: UUID,
    campaign_id: UUID,
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    attribution_model: AttributionModel = Query("last_touch"),
):
    _validate_window(date_from, date_to)
    await _load_campaign_or_404(store_id, campaign_id)
    async with AsyncSessionLocal() as session:
        analytics = AnalyticsRepository(session)
        rows = await analytics.campaign_breakdown_channel(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )
    return SuccessResponse(
        data=CampaignBreakdownChannelResponse(
            campaign_id=str(campaign_id),
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            attribution_model=attribution_model,
            channels=[_ChannelRow(**r) for r in rows],
        ),
        message="Channel breakdown for campaign",
    )


@router.get(
    "/{campaign_id}/breakdown/utm",
    response_model=SuccessResponse[CampaignBreakdownUtmResponse],
    summary="Top UTM combos by sessions + sales",
    operation_id="get_campaign_breakdown_utm",
)
async def get_campaign_breakdown_utm(
    store_id: UUID,
    campaign_id: UUID,
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    attribution_model: AttributionModel = Query("last_touch"),
    limit: int = Query(20, ge=1, le=100),
):
    _validate_window(date_from, date_to)
    await _load_campaign_or_404(store_id, campaign_id)
    async with AsyncSessionLocal() as session:
        analytics = AnalyticsRepository(session)
        rows = await analytics.campaign_breakdown_utm(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
    return SuccessResponse(
        data=CampaignBreakdownUtmResponse(
            campaign_id=str(campaign_id),
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            attribution_model=attribution_model,
            combos=[_UtmComboRow(**r) for r in rows],
        ),
        message="UTM combo breakdown for campaign",
    )


@router.get(
    "/{campaign_id}/breakdown/customer-type",
    response_model=SuccessResponse[CampaignBreakdownCustomerTypeResponse],
    summary="Orders + sales by new vs returning customers",
    operation_id="get_campaign_breakdown_customer_type",
)
async def get_campaign_breakdown_customer_type(
    store_id: UUID,
    campaign_id: UUID,
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    attribution_model: AttributionModel = Query("last_touch"),
):
    """SEC-007: response shape exposes ONLY aggregates. The repo method
    reads ``customers.first_touch_attribution`` JSONB internally for
    the new-vs-returning classification but never surfaces it.
    """
    _validate_window(date_from, date_to)
    await _load_campaign_or_404(store_id, campaign_id)
    async with AsyncSessionLocal() as session:
        analytics = AnalyticsRepository(session)
        data = await analytics.campaign_breakdown_customer_type(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )
    return SuccessResponse(
        data=CampaignBreakdownCustomerTypeResponse(
            campaign_id=str(campaign_id),
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            attribution_model=attribution_model,
            new_customers=_CustomerTypeBlock(**data["new_customers"]),
            returning_customers=_CustomerTypeBlock(**data["returning_customers"]),
        ),
        message="Customer-type breakdown for campaign",
    )


@router.get(
    "/{campaign_id}/breakdown/order-size",
    response_model=SuccessResponse[CampaignBreakdownOrderSizeResponse],
    summary="Histogram of order totals (10 fixed bins)",
    operation_id="get_campaign_breakdown_order_size",
)
async def get_campaign_breakdown_order_size(
    store_id: UUID,
    campaign_id: UUID,
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    attribution_model: AttributionModel = Query("last_touch"),
):
    _validate_window(date_from, date_to)
    await _load_campaign_or_404(store_id, campaign_id)
    async with AsyncSessionLocal() as session:
        analytics = AnalyticsRepository(session)
        bins = await analytics.campaign_breakdown_order_size(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )
    return SuccessResponse(
        data=CampaignBreakdownOrderSizeResponse(
            campaign_id=str(campaign_id),
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            attribution_model=attribution_model,
            bins=[_OrderSizeBin(**b) for b in bins],
        ),
        message="Order-size histogram for campaign",
    )


@router.get(
    "/{campaign_id}/breakdown/device",
    response_model=SuccessResponse[CampaignBreakdownDeviceResponse],
    summary="Sessions by device class",
    operation_id="get_campaign_breakdown_device",
)
async def get_campaign_breakdown_device(
    store_id: UUID,
    campaign_id: UUID,
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    attribution_model: AttributionModel = Query("last_touch"),
):
    _validate_window(date_from, date_to)
    await _load_campaign_or_404(store_id, campaign_id)
    async with AsyncSessionLocal() as session:
        analytics = AnalyticsRepository(session)
        rows = await analytics.campaign_breakdown_device(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )
    return SuccessResponse(
        data=CampaignBreakdownDeviceResponse(
            campaign_id=str(campaign_id),
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            attribution_model=attribution_model,
            devices=[_DeviceRow(**r) for r in rows],
        ),
        message="Device breakdown for campaign",
    )


# ── Tips (feature 002 US8) ───────────────────────────────────────


class TipResponse(BaseModel):
    id: str
    severity: str
    title: str
    body: str
    data: dict


class CampaignTipsResponse(BaseModel):
    campaign_id: str
    date_from: str
    date_to: str
    attribution_model: str
    tips: list[TipResponse]


@router.get(
    "/{campaign_id}/tips",
    response_model=SuccessResponse[CampaignTipsResponse],
    summary="Heuristic optimization tips for a campaign",
    operation_id="get_campaign_tips",
)
async def get_campaign_tips(
    store_id: UUID,
    campaign_id: UUID,
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    attribution_model: AttributionModel = Query("last_touch"),
):
    """Compute heuristic tips from existing aggregations.

    Reuses the breakdown queries we already have — channel, customer
    type, device, coupon stats (from performance), top products (from
    performance). No new aggregation method or external call.
    """
    from src.application.services.campaign_tips import compute_tips

    _validate_window(date_from, date_to)
    await _load_campaign_or_404(store_id, campaign_id)
    async with AsyncSessionLocal() as session:
        analytics = AnalyticsRepository(session)
        channel = await analytics.campaign_breakdown_channel(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )
        customer_type = await analytics.campaign_breakdown_customer_type(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )
        device = await analytics.campaign_breakdown_device(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )
        perf = await analytics.campaign_performance(
            store_id=store_id,
            campaign_id=campaign_id,
            date_from=date_from,
            date_to=date_to,
        )

    tips = compute_tips(
        channel_breakdown=channel,
        customer_type_breakdown=customer_type,
        device_breakdown=device,
        coupon_redemptions=perf.get("coupon_redemptions", 0),
        coupon_revenue_cents=sum(
            c.get("revenue_cents", 0) for c in perf.get("coupon_breakdown", [])
        ),
        total_revenue_cents=perf.get("revenue_cents", 0),
        top_products=perf.get("top_products", []),
    )

    return SuccessResponse(
        data=CampaignTipsResponse(
            campaign_id=str(campaign_id),
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            attribution_model=attribution_model,
            tips=[TipResponse(**t.to_dict()) for t in tips],
        ),
        message="Tips for campaign",
    )


# ── Campaign-attached coupons ─────────────────────────────────────


class IssueCampaignCouponRequest(BaseModel):
    """Body for ``POST /campaigns/{id}/coupons``.

    Mirrors the relevant subset of the standalone-coupon CRUD: the
    code is auto-generated from the campaign name; the merchant only
    chooses the discount mechanics.
    """

    coupon_type: Literal["percentage", "fixed"]
    # Decimal in store currency — percentage when coupon_type=percentage
    # (0-100), absolute discount when coupon_type=fixed.
    value: float = Field(gt=0, le=100_000)
    min_order_amount: float | None = Field(default=None, ge=0)
    max_discount_amount: float | None = Field(default=None, ge=0)
    usage_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def _enforce_percentage_range(self) -> IssueCampaignCouponRequest:
        if self.coupon_type == "percentage" and self.value > 100:
            raise ValueError("percentage coupons cannot exceed 100")
        return self


class CampaignCouponResponse(BaseModel):
    id: str
    code: str
    coupon_type: str
    value: float
    min_order_amount: float | None = None
    max_discount_amount: float | None = None
    usage_limit: int | None = None
    usage_count: int
    valid_from: str | None = None
    valid_until: str | None = None
    is_active: bool
    campaign_id: str | None = None
    created_at: str


def _coupon_to_response(c: Coupon) -> CampaignCouponResponse:
    return CampaignCouponResponse(
        id=str(c.id),
        code=c.code,
        coupon_type=c.coupon_type.value,
        value=float(c.value),
        # `is not None` (not truthiness) because Decimal('0') is falsy
        # in Python — a merchant who set min_order_amount=0 explicitly
        # was getting `null` back instead of `0.0`. Same for the cap.
        min_order_amount=float(c.min_order_amount)
        if c.min_order_amount is not None
        else None,
        max_discount_amount=float(c.max_discount_amount)
        if c.max_discount_amount is not None
        else None,
        usage_limit=c.usage_limit,
        usage_count=c.usage_count,
        valid_from=c.valid_from.isoformat() if c.valid_from else None,
        valid_until=c.valid_until.isoformat() if c.valid_until else None,
        is_active=c.is_active,
        campaign_id=str(c.campaign_id) if c.campaign_id else None,
        created_at=c.created_at.isoformat() if c.created_at else "",
    )


@router.post(
    "/{campaign_id}/coupons",
    response_model=SuccessResponse[CampaignCouponResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Issue a discount code attached to a campaign",
    operation_id="issue_campaign_coupon",
)
async def issue_campaign_coupon(
    store_id: UUID,
    campaign_id: UUID,
    body: IssueCampaignCouponRequest,
    user_id: UUID = Depends(get_current_user_id),
):
    """Mint a coupon linked to ``campaign_id``.

    Code is auto-generated as ``<CAMPAIGN-SLUG>-<6-char-Crockford>``
    so a merchant scanning their coupons list can eyeball which
    campaign each code belongs to. The campaign_id is stamped onto
    every order that redeems this code (when no UTM-resolved
    attribution wins first), so direct-traffic conversions still
    attribute back to the campaign.

    SEC-001: campaign lookup is filtered by ``(id, store_id)`` so
    cross-tenant probes 404 (not 403).
    """
    from decimal import Decimal as _D

    async with AsyncSessionLocal() as session:
        camp_repo = MarketingCampaignRepository(session)
        campaign = await camp_repo.get_by_id(campaign_id)
        if campaign is None or campaign.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        code = await campaign_coupon_service.generate_unique_code(
            session=session,
            store_id=store_id,
            campaign_name=campaign.name,
        )

        try:
            entity = campaign_coupon_service.build_campaign_coupon(
                store_id=store_id,
                tenant_id=campaign.tenant_id,
                campaign=campaign,
                code=code,
                coupon_type=CouponType(body.coupon_type),
                value=_D(str(body.value)),
                min_order_amount=_D(str(body.min_order_amount))
                if body.min_order_amount is not None
                else None,
                max_discount_amount=_D(str(body.max_discount_amount))
                if body.max_discount_amount is not None
                else None,
                usage_limit=body.usage_limit,
                valid_from=body.valid_from,
                valid_until=body.valid_until,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        coupon_repo = CouponRepository(session)
        created = await coupon_repo.create(entity)
        await session.commit()

    # SEC-008: audit-log the issuance. Discount codes are money on the
    # ground — a paper trail of who/when matters for fraud
    # investigation and merchant disputes.
    async with AsyncSessionLocal() as audit_session:
        await AuditService(audit_session).log(
            event_type=EventType.COUPON_CREATE,
            action="issue_campaign_coupon",
            resource_type="coupon",
            resource_id=str(created.id),
            user_id=user_id,
            store_id=store_id,
            tenant_id=campaign.tenant_id,
            details={
                "campaign_id": str(campaign_id),
                "code": created.code,
                "coupon_type": created.coupon_type.value,
                "value": float(created.value),
            },
        )
        await audit_session.commit()

    return SuccessResponse(
        data=_coupon_to_response(created),
        message="Campaign coupon issued",
    )


@router.get(
    "/{campaign_id}/coupons",
    response_model=SuccessResponse[list[CampaignCouponResponse]],
    summary="List discount codes attached to a campaign",
    operation_id="list_campaign_coupons",
)
async def list_campaign_coupons(
    store_id: UUID,
    campaign_id: UUID,
):
    """Read-only view of all coupons attached to ``campaign_id``.

    Drives the hub's "Codes issued under this campaign" panel. Returns
    them in creation order so the most recently issued code is at the
    top — that's typically what the merchant just clicked the Issue
    button for and wants to copy.
    """
    async with AsyncSessionLocal() as session:
        camp_repo = MarketingCampaignRepository(session)
        campaign = await camp_repo.get_by_id(campaign_id)
        if campaign is None or campaign.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        # Direct query — no list-by-campaign method on CouponRepository
        # yet. Keep it close to the existing patterns: select * where
        # store + campaign, ordered by created_at DESC.
        from sqlalchemy import select as _select

        from src.infrastructure.database.connection import (
            get_tenant_id as _get_tenant_id,
        )
        from src.infrastructure.database.models.tenant.coupon import (
            CouponModel as _CouponModel,
        )

        coupon_query = (
            _select(_CouponModel)
            .where(
                _CouponModel.store_id == store_id,
                _CouponModel.campaign_id == campaign_id,
            )
            .order_by(_CouponModel.created_at.desc())
        )
        # Defense in depth: even though store_id + campaign_id ought
        # to be enough to scope to one tenant (campaigns are
        # store-scoped, stores are tenant-scoped), every other repo
        # method on this table also filters by tenant_id. Apply the
        # same filter here so a future bug elsewhere — e.g. a
        # campaign_id resolution that leaks across tenants — can't
        # turn this into a data-disclosure path.
        _tid = _get_tenant_id()
        if _tid:
            coupon_query = coupon_query.where(_CouponModel.tenant_id == _tid)

        result = await session.execute(coupon_query)
        models = result.scalars().all()
        coupon_repo = CouponRepository(session)
        entities = [coupon_repo._to_entity(m) for m in models]

    return SuccessResponse(
        data=[_coupon_to_response(c) for c in entities],
        message="Campaign coupons listed",
    )


# ── Promote-on-Meta (spec 005 US7) ──────────────────────────────


class PromoteOnMetaResponse(BaseModel):
    ad_id: str
    creative_id: str
    ads_manager_url: str
    used_custom_audience_id: str | None = None


@router.post(
    "/{campaign_id}/promote-on-meta",
    response_model=SuccessResponse[PromoteOnMetaResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Fork a completed campaign into a PAUSED Meta ad draft",
    operation_id="promote_campaign_on_meta",
)
async def promote_campaign_on_meta_route(
    store_id: UUID,
    campaign_id: UUID,
):
    """Create a PAUSED Meta ad mirroring the campaign's creative.

    Gated on:
      - Campaign in ``completed`` state.
      - Store has Meta connected (ad_account_id + page_id + valid token).
      - Token has ``ads_management`` scope.

    Targeting defaults to the synced Custom Audience for the campaign's
    segment when one exists; otherwise empty + Egypt geo. Always
    PAUSED so the merchant sets budget / schedule / bid in Meta Ads
    Manager before publishing — NUMU never instructs Meta to charge.
    """
    from sqlalchemy import select as _select

    from src.application.services.meta_ad_promote_service import (
        promote_campaign_on_meta,
    )
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )
    from src.infrastructure.repositories import StoreRepository

    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        c = await repo.get_by_id(campaign_id)
        if c is None or c.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
            )
        if c.status != CampaignStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Can only promote completed campaigns to Meta — this "
                    f"one is '{c.status.value}'."
                ),
            )

        store_repo_local = StoreRepository(session)
        store = await store_repo_local.get_by_id(store_id)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
            )

        meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
        ad_account_id = meta_cfg.get("ad_account_id")
        page_id = meta_cfg.get("page_id")
        if not (ad_account_id and page_id):
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=(
                    "Meta is not fully connected — both ad_account_id "
                    "and page_id are required. Complete the OAuth picker "
                    "in Settings → Integrations → Meta."
                ),
            )

        cred_q = (
            _select(ServiceCredential)
            .where(ServiceCredential.tenant_id == store.tenant_id)
            .where(ServiceCredential.service_type == ServiceType.TRACKING)
            .where(ServiceCredential.service_name == ServiceName.META_CAPI)
            .where(ServiceCredential.is_active.is_(True))
        )
        cred = (await session.execute(cred_q)).scalar_one_or_none()
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Meta access token is missing or expired.",
            )
        try:
            sm = get_secrets_manager()
            decrypted = await sm.decrypt(
                cred.credentials_encrypted, cred.encryption_key_id
            )
            access_token = (decrypted or {}).get("access_token")
        except Exception:
            access_token = None
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Meta access token could not be decrypted.",
            )

        # Creative resolution: prefer promoted_item snapshot, fall back
        # to subject/body for legacy campaigns with no promoted_item.
        promoted = c.promoted_item or {}
        snapshot = promoted.get("snapshot") or {}
        headline = snapshot.get("name") or c.inline_subject or c.name
        image_url = snapshot.get("image_url") or store.logo_url or ""
        link_url = snapshot.get("url") or (
            f"https://{store.subdomain}.numueg.app/"
            if store.subdomain
            else "https://numueg.app/"
        )
        body_text = c.inline_subject or c.name

        custom_audience_id: str | None = None
        af = c.audience_filter or {}
        seg_key = af.get("segment_key") if isinstance(af, dict) else None
        if seg_key:
            ca_cache = (meta_cfg.get("custom_audiences") or {}).get(seg_key) or {}
            custom_audience_id = ca_cache.get("audience_id")

        result = await promote_campaign_on_meta(
            ad_account_id=ad_account_id,
            page_id=page_id,
            access_token=access_token,
            campaign_name=c.name,
            headline=headline,
            body_text=body_text,
            image_url=image_url,
            link_url=link_url,
            custom_audience_id=custom_audience_id,
        )

        # Service now returns a structured ``{"error": "<Meta message>"}``
        # on failure so we can surface Meta's actual reason. Opaque 502s
        # left the merchant guessing which of the three canned causes
        # applied (commonly: ads_management scope missing — subcode 33).
        if "error" in result:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=result["error"],
            )

        try:
            await AuditService(session).log(
                event_type=EventType.ADMIN_CONFIG_CHANGE,
                action="meta_promote_campaign",
                resource_type="marketing_campaign",
                resource_id=str(campaign_id),
                store_id=store_id,
                tenant_id=store.tenant_id,
                new_value={
                    "campaign_id": str(campaign_id),
                    "campaign_name": c.name,
                    "meta_ad_id": result["ad_id"],
                    "meta_creative_id": result["creative_id"],
                    "ad_account_id": ad_account_id,
                    "used_custom_audience_id": custom_audience_id,
                },
            )
            await session.commit()
        except Exception:
            logger.warning(
                "promote_campaign_audit_log_failed",
                extra={"campaign_id": str(campaign_id)},
                exc_info=True,
            )

    return SuccessResponse(
        data=PromoteOnMetaResponse(
            ad_id=result["ad_id"],
            creative_id=result["creative_id"],
            ads_manager_url=result["ads_manager_url"],
            used_custom_audience_id=custom_audience_id,
        ),
        message="Draft ad created in Meta Ads Manager (PAUSED)",
    )
