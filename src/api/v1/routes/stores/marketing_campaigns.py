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
from src.application.services import short_link_service
from src.application.services.audit_service import AuditService, EventType
from src.application.services.link_builder import LinkBuilder
from src.application.services.short_code_generator import (
    generate as generate_short_code,
)
from src.config import settings
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
    return SuccessResponse(data=_to_response(updated), message="Campaign canceled")


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
    # ``with_short_link=true``. Shape: ``https://numueg.app/r/{code}``.
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
                short_url = (
                    f"https://{settings.storefront_base_domain}/r/{short_url_code}"
                )
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
            ),
        ),
        message="Campaign performance retrieved",
    )
