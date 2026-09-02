"""Admin merchant-leads endpoint.

URL: /api/v1/admin/leads
Requires SUPER_ADMIN role.

Every person who reached for NUMU through either front door, whether or
not they became a tenant. This is the table sales works from — the demos
list next door only ever showed leads that still had a live demo tenant,
which is to say it hid every lead the cleanup task had already deleted.

The channel breakdown on ``/stats`` is the first time the platform can
answer "which channel produced this merchant". It is honest about the
gap: leads recorded before attribution shipped have no UTMs and are
counted under ``unattributed`` rather than silently folded into direct.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel

router = APIRouter()


class LeadRowResponse(BaseModel):
    id: UUID
    email: str
    name: str | None = None
    phone: str | None = None
    source: str
    last_source: str | None = None
    status: str
    plan_intent: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    referrer: str | None = None
    landing_path: str | None = None
    tenant_id: UUID | None = None
    store_subdomain: str | None = None
    demo_started_at: datetime | None = None
    registered_at: datetime | None = None
    store_created_at: datetime | None = None
    first_order_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    # Convenience for the admin UI: a lead whose tenant is gone still
    # matters, and the UI should not have to guess why the link is dead.
    has_phone: bool


class ChannelRow(BaseModel):
    channel: str
    leads: int
    stores_created: int


class LeadStatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    with_phone: int
    channels: list[ChannelRow]


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[LeadRowResponse]],
    summary="List merchant leads",
    operation_id="list_merchant_leads",
)
async def list_leads(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_filter: Literal[
        "all", "new", "demo_started", "registered", "store_created", "activated"
    ] = Query("all", alias="status"),
    source: Literal["all", "demo", "signup"] = Query("all"),
    utm_source: str | None = Query(None, max_length=120),
    has_phone: bool | None = Query(None),
    q: str | None = Query(None, max_length=160, description="Email or name contains"),
):
    base = select(MerchantLeadModel)

    if status_filter != "all":
        base = base.where(MerchantLeadModel.status == status_filter)
    if source != "all":
        base = base.where(MerchantLeadModel.source == source)
    if utm_source:
        base = base.where(MerchantLeadModel.utm_source == utm_source)
    if has_phone is True:
        base = base.where(MerchantLeadModel.phone.isnot(None))
    elif has_phone is False:
        base = base.where(MerchantLeadModel.phone.is_(None))
    if q:
        # Escape the LIKE wildcards a search box will happily contain, so
        # a merchant searching for "100%" does not match every row.
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{needle}%"
        base = base.where(
            or_(
                MerchantLeadModel.email.ilike(pattern, escape="\\"),
                MerchantLeadModel.name.ilike(pattern, escape="\\"),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                base.order_by(MerchantLeadModel.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    items = [
        LeadRowResponse(
            id=lead.id,
            email=lead.email,
            name=lead.name,
            phone=lead.phone,
            source=lead.source,
            last_source=lead.last_source,
            status=lead.status,
            plan_intent=lead.plan_intent,
            utm_source=lead.utm_source,
            utm_medium=lead.utm_medium,
            utm_campaign=lead.utm_campaign,
            referrer=lead.referrer,
            landing_path=lead.landing_path,
            tenant_id=lead.tenant_id,
            store_subdomain=lead.store_subdomain,
            demo_started_at=lead.demo_started_at,
            registered_at=lead.registered_at,
            store_created_at=lead.store_created_at,
            first_order_at=lead.first_order_at,
            last_seen_at=lead.last_seen_at,
            created_at=lead.created_at,
            has_phone=bool(lead.phone),
        )
        for lead in rows
    ]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
        ),
        message="Merchant leads retrieved",
    )


@router.get(
    "/stats",
    response_model=SuccessResponse[LeadStatsResponse],
    summary="Lead funnel and channel breakdown",
    operation_id="merchant_lead_stats",
)
async def lead_stats(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    total = (
        await db.execute(select(func.count()).select_from(MerchantLeadModel))
    ).scalar_one()

    status_rows = (
        await db.execute(
            select(MerchantLeadModel.status, func.count()).group_by(
                MerchantLeadModel.status
            )
        )
    ).all()

    with_phone = (
        await db.execute(
            select(func.count())
            .select_from(MerchantLeadModel)
            .where(MerchantLeadModel.phone.isnot(None))
        )
    ).scalar_one()

    # "unattributed" is not "direct": it is every lead recorded before
    # the landing page started sending UTMs, plus genuine direct traffic.
    # Collapsing the two would overstate direct for months.
    channel_rows = (
        await db.execute(
            select(
                func.coalesce(MerchantLeadModel.utm_source, "unattributed"),
                func.count(),
                func.count(MerchantLeadModel.store_created_at),
            ).group_by(func.coalesce(MerchantLeadModel.utm_source, "unattributed"))
        )
    ).all()

    return SuccessResponse(
        data=LeadStatsResponse(
            total=int(total or 0),
            by_status={str(s): int(c) for s, c in status_rows},
            with_phone=int(with_phone or 0),
            channels=sorted(
                [
                    ChannelRow(
                        channel=str(channel),
                        leads=int(leads),
                        stores_created=int(stores),
                    )
                    for channel, leads, stores in channel_rows
                ],
                key=lambda r: r.leads,
                reverse=True,
            ),
        ),
        message="Lead statistics retrieved",
    )
