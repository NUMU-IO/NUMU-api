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
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.infrastructure.database.models.public.merchant_business_profile import (
    MerchantBusinessProfileModel,
)
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel
from src.infrastructure.database.models.public.tenant import TenantModel

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

    # ── Reachability ──
    whatsapp_phone: str | None = None
    language: str | None = None

    # ── Qualification ──
    sells_what: str | None = None
    sells_where_today: str | None = None
    monthly_orders_band: str | None = None
    city: str | None = None

    # ── What they actually became ──
    # `plan_intent` is the card they clicked on the landing page; this is
    # the plan they are really on. The two diverging is itself a signal.
    tenant_plan: str | None = None
    tenant_lifecycle: str | None = None

    # ── Commercial readiness ──
    is_registered_business: bool | None = None
    has_payout_account: bool = False
    business_complete: bool = False

    # ── Lifecycle ──
    demo_started_at: datetime | None = None
    registered_at: datetime | None = None
    store_created_at: datetime | None = None
    first_product_at: datetime | None = None
    first_order_at: datetime | None = None
    first_commission_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    # Convenience for the admin UI: a lead whose tenant is gone still
    # matters, and the UI should not have to guess why the link is dead.
    has_phone: bool


class ChannelRow(BaseModel):
    channel: str
    leads: int
    stores_created: int


class FunnelResponse(BaseModel):
    """Absolute counts down the lifecycle, not percentages.

    Each step counts leads that reached it *ever*, so the numbers only
    ever decrease down the list. Percentages are left to the UI, which
    knows which denominator it is showing.
    """

    leads: int
    registered: int
    store_created: int
    first_product: int
    activated: int
    paying: int


class LeadStatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    with_phone: int
    channels: list[ChannelRow]
    funnel: FunnelResponse
    # Qualification breakdowns — what our merchants actually are.
    by_sells_what: dict[str, int]
    by_orders_band: dict[str, int]
    by_sells_where: dict[str, int]
    # Actual tenant plan, not the intent clicked on the landing page.
    by_plan: dict[str, int]
    business_profiles_complete: int


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
    utm_campaign: str | None = Query(None, max_length=120),
    has_phone: bool | None = Query(None),
    # ── Qualification ──
    sells_what: str | None = Query(None, max_length=32, description="Merchant type"),
    sells_where_today: str | None = Query(None, max_length=32),
    monthly_orders_band: str | None = Query(None, max_length=20),
    city: str | None = Query(None, max_length=80),
    # ── Plan ──
    # `plan` is what the tenant is actually on; `plan_intent` is the card
    # they clicked before signing up. Filtering on the two separately is
    # how you find merchants who wanted payg and ended up somewhere else.
    plan: str | None = Query(None, max_length=32),
    plan_intent: str | None = Query(None, max_length=20),
    lifecycle_state: str | None = Query(None, max_length=32),
    # ── Commercial readiness ──
    is_registered_business: bool | None = Query(None),
    business_complete: bool | None = Query(None),
    # ── Lifecycle ──
    activated: bool | None = Query(None, description="Has a first paid order"),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    sort: Literal[
        "created_desc", "created_asc", "last_seen_desc", "activated_desc"
    ] = Query("created_desc"),
    q: str | None = Query(
        None, max_length=160, description="Email, name, phone or subdomain contains"
    ),
):
    # LEFT joins throughout: a lead whose tenant was deleted, or who never
    # created one, must still appear. An inner join here would silently
    # hide exactly the leads this table exists to preserve.
    base = (
        select(MerchantLeadModel, TenantModel, MerchantBusinessProfileModel)
        .outerjoin(TenantModel, TenantModel.id == MerchantLeadModel.tenant_id)
        .outerjoin(
            MerchantBusinessProfileModel,
            MerchantBusinessProfileModel.tenant_id == MerchantLeadModel.tenant_id,
        )
    )

    if status_filter != "all":
        base = base.where(MerchantLeadModel.status == status_filter)
    if source != "all":
        base = base.where(MerchantLeadModel.source == source)
    if utm_source:
        base = base.where(MerchantLeadModel.utm_source == utm_source)
    if utm_campaign:
        base = base.where(MerchantLeadModel.utm_campaign == utm_campaign)
    if has_phone is True:
        base = base.where(MerchantLeadModel.phone.isnot(None))
    elif has_phone is False:
        base = base.where(MerchantLeadModel.phone.is_(None))

    if sells_what:
        base = base.where(MerchantLeadModel.sells_what == sells_what)
    if sells_where_today:
        base = base.where(MerchantLeadModel.sells_where_today == sells_where_today)
    if monthly_orders_band:
        base = base.where(MerchantLeadModel.monthly_orders_band == monthly_orders_band)
    if city:
        base = base.where(MerchantLeadModel.city.ilike(f"%{city.strip()}%"))

    if plan:
        base = base.where(TenantModel.plan == plan)
    if plan_intent:
        base = base.where(MerchantLeadModel.plan_intent == plan_intent)
    if lifecycle_state:
        base = base.where(TenantModel.lifecycle_state == lifecycle_state)

    if is_registered_business is not None:
        base = base.where(
            MerchantBusinessProfileModel.is_registered_business
            == is_registered_business
        )
    if business_complete is True:
        # Mirrors MerchantBusinessProfileModel.is_complete in SQL. Kept in
        # step with that property by the test in test_admin_leads_filters.
        base = base.where(
            MerchantBusinessProfileModel.payout_encrypted.isnot(None),
            MerchantBusinessProfileModel.is_registered_business.isnot(None),
            or_(
                MerchantBusinessProfileModel.is_registered_business.is_(False),
                MerchantBusinessProfileModel.tax_id.isnot(None),
            ),
        )
    elif business_complete is False:
        base = base.where(
            or_(
                MerchantBusinessProfileModel.tenant_id.is_(None),
                MerchantBusinessProfileModel.payout_encrypted.is_(None),
                MerchantBusinessProfileModel.is_registered_business.is_(None),
                and_(
                    MerchantBusinessProfileModel.is_registered_business.is_(True),
                    MerchantBusinessProfileModel.tax_id.is_(None),
                ),
            )
        )

    if activated is True:
        base = base.where(MerchantLeadModel.first_order_at.isnot(None))
    elif activated is False:
        base = base.where(MerchantLeadModel.first_order_at.is_(None))

    if created_from:
        base = base.where(MerchantLeadModel.created_at >= created_from)
    if created_to:
        base = base.where(MerchantLeadModel.created_at <= created_to)
    if q:
        # Escape the LIKE wildcards a search box will happily contain, so
        # a merchant searching for "100%" does not match every row.
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{needle}%"
        base = base.where(
            or_(
                MerchantLeadModel.email.ilike(pattern, escape="\\"),
                MerchantLeadModel.name.ilike(pattern, escape="\\"),
                MerchantLeadModel.phone.ilike(pattern, escape="\\"),
                MerchantLeadModel.store_subdomain.ilike(pattern, escape="\\"),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    order_by = {
        "created_desc": MerchantLeadModel.created_at.desc(),
        "created_asc": MerchantLeadModel.created_at.asc(),
        "last_seen_desc": MerchantLeadModel.last_seen_at.desc().nullslast(),
        "activated_desc": MerchantLeadModel.first_order_at.desc().nullslast(),
    }[sort]

    # .scalars() would collapse each row to the lead and throw away the
    # joined tenant and profile, so the rows are read as tuples.
    rows = (
        await db.execute(
            base.order_by(order_by).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()

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
            whatsapp_phone=lead.whatsapp_phone,
            language=lead.language,
            sells_what=lead.sells_what,
            sells_where_today=lead.sells_where_today,
            monthly_orders_band=lead.monthly_orders_band,
            city=lead.city,
            tenant_plan=tenant.plan if tenant else None,
            tenant_lifecycle=tenant.lifecycle_state if tenant else None,
            is_registered_business=(
                profile.is_registered_business if profile else None
            ),
            has_payout_account=bool(profile and profile.has_payout_account),
            business_complete=bool(profile and profile.is_complete),
            demo_started_at=lead.demo_started_at,
            registered_at=lead.registered_at,
            store_created_at=lead.store_created_at,
            first_product_at=lead.first_product_at,
            first_order_at=lead.first_order_at,
            first_commission_at=lead.first_commission_at,
            last_seen_at=lead.last_seen_at,
            created_at=lead.created_at,
            has_phone=bool(lead.phone),
        )
        for lead, tenant, profile in rows
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

    async def _breakdown(column):
        """Count non-null values of *column*, biggest first."""
        rows = (
            await db.execute(
                select(column, func.count()).where(column.isnot(None)).group_by(column)
            )
        ).all()
        return {
            str(k): int(v) for k, v in sorted(rows, key=lambda r: r[1], reverse=True)
        }

    by_sells_what = await _breakdown(MerchantLeadModel.sells_what)
    by_orders_band = await _breakdown(MerchantLeadModel.monthly_orders_band)
    by_sells_where = await _breakdown(MerchantLeadModel.sells_where_today)

    plan_rows = (
        await db.execute(
            select(TenantModel.plan, func.count())
            .join(MerchantLeadModel, MerchantLeadModel.tenant_id == TenantModel.id)
            .group_by(TenantModel.plan)
        )
    ).all()

    # Counting a nullable timestamp counts the rows where it is set, which
    # is exactly "reached this step".
    funnel_row = (
        await db.execute(
            select(
                func.count(),
                func.count(MerchantLeadModel.registered_at),
                func.count(MerchantLeadModel.store_created_at),
                func.count(MerchantLeadModel.first_product_at),
                func.count(MerchantLeadModel.first_order_at),
                func.count(MerchantLeadModel.first_commission_at),
            ).select_from(MerchantLeadModel)
        )
    ).one()

    complete_profiles = (
        await db.execute(
            select(func.count())
            .select_from(MerchantBusinessProfileModel)
            .where(MerchantBusinessProfileModel.completed_at.isnot(None))
        )
    ).scalar_one()

    return SuccessResponse(
        data=LeadStatsResponse(
            total=int(total or 0),
            by_status={str(s): int(c) for s, c in status_rows},
            with_phone=int(with_phone or 0),
            funnel=FunnelResponse(
                leads=int(funnel_row[0] or 0),
                registered=int(funnel_row[1] or 0),
                store_created=int(funnel_row[2] or 0),
                first_product=int(funnel_row[3] or 0),
                activated=int(funnel_row[4] or 0),
                paying=int(funnel_row[5] or 0),
            ),
            by_sells_what=by_sells_what,
            by_orders_band=by_orders_band,
            by_sells_where=by_sells_where,
            by_plan={str(k): int(v) for k, v in plan_rows if k is not None},
            business_profiles_complete=int(complete_profiles or 0),
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
