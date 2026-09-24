"""Partner portal (partners.numueg.app): dashboard, webhook deliveries, team.

URL: /api/v1/partners. Hidden while the Partner program is closed, like the
rest of the portal. Every read is scoped to the caller's partner through
``partner_context``, so a team member sees the partner's apps and nothing
else.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.partners import (
    PartnerContext,
    partner_context,
    require_approved_partner,
    require_partner_manager,
    require_partner_program,
)
from src.api.dependencies.services import get_email_service
from src.api.middleware.token_activity import (
    APP_RETENTION_DAYS,
    app_hourly,
    app_log_entries,
    p95_from_buckets,
)
from src.api.responses import SuccessResponse
from src.application.services.partner_program import partner_membership
from src.config import settings
from src.core.entities.webhook import WebhookDeliveryStatus
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppUninstallEventModel,
)
from src.infrastructure.database.models.public.app_billing import (
    AppSubscriptionModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerMemberModel,
)
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.webhook import (
    WebhookDeliveryLogModel,
    WebhookSubscriptionModel,
)

logger = get_logger(__name__)

router = APIRouter(
    prefix="/partners",
    tags=["Partner Portal"],
    dependencies=[Depends(require_partner_program)],
)

INVITE_TTL = timedelta(days=7)


def _apps_of(owner_id: UUID, app_id: UUID | None):
    q = select(AppModel.id).where(AppModel.developer_id == owner_id)
    return q.where(AppModel.id == app_id) if app_id else q


def _in_range(column, start: date | None, end: date | None) -> list:
    conds = []
    if start:
        conds.append(column >= datetime.combine(start, time.min, UTC))
    if end:
        conds.append(column < datetime.combine(end + timedelta(days=1), time.min, UTC))
    return conds


def _install_status(is_enabled: bool, state: str | None) -> str:
    if not is_enabled:
        return "disabled"
    return "active" if (state or "active") == "active" else "pending"


# ─── Dashboard ────────────────────────────────────────────────────


class LatestInstall(BaseModel):
    store_name: str | None
    app_id: UUID
    app_name: str
    installed_at: datetime
    status: str


class MonthBucket(BaseModel):
    month: str
    installs: int


class PartnerDashboard(BaseModel):
    installs_total: int
    active: int
    disabled: int
    pending: int
    #: Counted from the day uninstalls started being recorded.
    uninstalled: int
    monthly: list[MonthBucket]
    latest: list[LatestInstall]


@router.get(
    "/me/dashboard",
    response_model=SuccessResponse[PartnerDashboard],
    operation_id="get_partner_dashboard",
)
async def dashboard(
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    app_id: UUID | None = None,
):
    apps = _apps_of(owner_id, app_id)
    rows = (
        await db.execute(
            select(
                AppInstallationModel.created_at,
                AppInstallationModel.is_enabled,
                AppInstallationModel.status,
                AppModel.id,
                AppModel.name,
                StoreModel.name,
            )
            .join(AppModel, AppModel.id == AppInstallationModel.app_id)
            .outerjoin(StoreModel, StoreModel.id == AppInstallationModel.store_id)
            .where(
                AppInstallationModel.app_id.in_(apps),
                *_in_range(AppInstallationModel.created_at, start, end),
            )
            .order_by(AppInstallationModel.created_at.desc())
        )
    ).all()
    states = Counter(_install_status(r[1], r[2]) for r in rows)
    months = Counter(r[0].strftime("%Y-%m") for r in rows)
    uninstalled = await db.scalar(
        select(func.count(AppUninstallEventModel.id)).where(
            AppUninstallEventModel.app_id.in_(apps),
            *_in_range(AppUninstallEventModel.created_at, start, end),
        )
    )
    return SuccessResponse(
        data=PartnerDashboard(
            installs_total=len(rows),
            active=states["active"],
            disabled=states["disabled"],
            pending=states["pending"],
            uninstalled=uninstalled or 0,
            monthly=[
                MonthBucket(month=m, installs=n) for m, n in sorted(months.items())
            ],
            latest=[
                LatestInstall(
                    store_name=store_name,
                    app_id=aid,
                    app_name=app_name,
                    installed_at=created_at,
                    status=_install_status(enabled, state),
                )
                for created_at, enabled, state, aid, app_name, store_name in rows[:10]
            ],
        )
    )


# ─── Webhook deliveries ───────────────────────────────────────────


class DeliveryOut(BaseModel):
    id: UUID
    app_id: UUID
    app_name: str
    event: str
    store_id: UUID
    store_name: str | None
    url: str
    status: str
    status_code: int | None
    attempts: int
    error: str | None
    next_attempt_at: datetime | None
    last_attempt_at: datetime | None
    created_at: datetime


class DeliveryPage(BaseModel):
    items: list[DeliveryOut]
    total: int
    page: int
    page_size: int


def _owned_deliveries(owner_id: UUID):
    return (
        select(
            WebhookDeliveryLogModel,
            WebhookSubscriptionModel,
            AppModel.id,
            AppModel.name,
            StoreModel.name,
        )
        .join(
            WebhookSubscriptionModel,
            WebhookSubscriptionModel.id == WebhookDeliveryLogModel.subscription_id,
        )
        .join(
            AppInstallationModel,
            AppInstallationModel.id == WebhookSubscriptionModel.app_installation_id,
        )
        .join(AppModel, AppModel.id == AppInstallationModel.app_id)
        .outerjoin(StoreModel, StoreModel.id == WebhookDeliveryLogModel.store_id)
        .where(AppModel.developer_id == owner_id)
    )


def _delivery(row) -> DeliveryOut:
    log, sub, app_id, app_name, store_name = row
    return DeliveryOut(
        id=log.id,
        app_id=app_id,
        app_name=app_name,
        event=log.event_type,
        store_id=log.store_id,
        store_name=store_name,
        url=sub.url,
        status=log.status,
        status_code=log.last_status_code,
        attempts=log.attempt_count,
        error=log.last_error,
        next_attempt_at=log.next_attempt_at,
        last_attempt_at=log.last_attempt_at,
        created_at=log.created_at,
    )


@router.get(
    "/me/webhooks/deliveries",
    response_model=SuccessResponse[DeliveryPage],
    operation_id="list_partner_webhook_deliveries",
)
async def list_deliveries(
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    app_id: UUID | None = None,
    event: str | None = None,
    delivery_status: Annotated[
        WebhookDeliveryStatus | None, Query(alias="status")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
):
    q = _owned_deliveries(owner_id)
    if app_id:
        q = q.where(AppModel.id == app_id)
    if event:
        q = q.where(WebhookDeliveryLogModel.event_type == event)
    if delivery_status:
        q = q.where(WebhookDeliveryLogModel.status == delivery_status.value)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = (
        await db.execute(
            q.order_by(WebhookDeliveryLogModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return SuccessResponse(
        data=DeliveryPage(
            items=[_delivery(r) for r in rows],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/me/webhooks/deliveries/{delivery_id}/resend",
    response_model=SuccessResponse[DeliveryOut],
    operation_id="resend_partner_webhook_delivery",
)
async def resend_delivery(
    delivery_id: UUID,
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Queue the delivery again. The webhook retry poller sends it within
    seconds, signed and guarded exactly like every other attempt."""
    row = (
        await db.execute(
            _owned_deliveries(owner_id).where(WebhookDeliveryLogModel.id == delivery_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    log, sub = row[0], row[1]
    if log.status == WebhookDeliveryStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="This delivery is already queued.")
    if not sub.is_active:
        raise HTTPException(
            status_code=409,
            detail="This endpoint was switched off after repeated failures.",
        )
    log.status = WebhookDeliveryStatus.PENDING.value
    log.next_attempt_at = datetime.now(UTC)
    log.exhausted_at = None
    await db.flush()
    logger.info("partner_webhook_resend", delivery_id=str(log.id))
    return SuccessResponse(data=_delivery(row), message="Delivery queued")


# ─── Analytics ────────────────────────────────────────────────────


class AnalyticsMonth(BaseModel):
    month: str
    installs: int
    uninstalls: int
    #: Stores with the app installed at the end of the month.
    active_stores: int
    #: Uninstalls over stores installed at the start of the month.
    churn_rate: float | None


class ReasonCount(BaseModel):
    reason: str
    count: int


class ReasonNote(BaseModel):
    app_id: UUID
    reason: str | None
    text: str
    created_at: datetime


class AppApiHealth(BaseModel):
    app_id: UUID
    app_name: str
    requests: int
    errors: int
    error_rate: float | None


class PartnerAnalytics(BaseModel):
    months: list[AnalyticsMonth]
    reasons: list[ReasonCount]
    notes: list[ReasonNote]
    paid_active: int
    #: Null until subscriptions carry a trial marker (see trial_note).
    trial_to_paid: float | None
    trial_note: str | None
    api: list[AppApiHealth]
    #: API counters are kept for 14 days, so their range is clipped to that.
    api_from: datetime | None


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _next_month(d: date) -> date:
    return (d.replace(day=1) + timedelta(days=32)).replace(day=1)


@router.get(
    "/me/analytics",
    response_model=SuccessResponse[PartnerAnalytics],
    operation_id="get_partner_analytics",
)
async def analytics(
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    app_id: UUID | None = None,
):
    """Installed stores, churn, uninstall reasons, subscriptions and API health.

    ponytail: every install and uninstall of the partner's apps is read and
    bucketed in Python (months x rows). Move to SQL generate_series when a
    partner reaches ~100k installs.
    """
    now = datetime.now(UTC)
    end = end or now.date()
    start = start or (end.replace(day=1) - timedelta(days=334)).replace(day=1)
    if start > end:
        raise HTTPException(status_code=422, detail="'from' must be before 'to'.")
    apps = (
        await db.execute(
            select(AppModel.id, AppModel.name).where(
                AppModel.id.in_(_apps_of(owner_id, app_id))
            )
        )
    ).all()
    ids = [a[0] for a in apps]
    installed = [
        _utc(c)
        for c in (
            await db.scalars(
                select(AppInstallationModel.created_at).where(
                    AppInstallationModel.app_id.in_(ids)
                )
            )
        ).all()
    ]
    events = (
        await db.scalars(
            select(AppUninstallEventModel)
            .where(AppUninstallEventModel.app_id.in_(ids))
            .order_by(AppUninstallEventModel.created_at.desc())
        )
    ).all()
    gone = [
        (_utc(e.installed_at) if e.installed_at else None, _utc(e.created_at))
        for e in events
    ]

    def active_at(t: datetime) -> int:
        return sum(c < t for c in installed) + sum(
            (i is None or i < t) and u >= t for i, u in gone
        )

    def between(values, a: datetime, b: datetime) -> int:
        return sum(v is not None and a <= v < b for v in values)

    months = []
    m = start.replace(day=1)
    while m <= end:
        m_start = datetime.combine(m, time.min, UTC)
        m_end = datetime.combine(_next_month(m), time.min, UTC)
        base = active_at(m_start)
        uninstalls = between([u for _, u in gone], m_start, m_end)
        months.append(
            AnalyticsMonth(
                month=m.strftime("%Y-%m"),
                installs=between(installed, m_start, m_end)
                + between([i for i, _ in gone], m_start, m_end),
                uninstalls=uninstalls,
                active_stores=active_at(min(m_end, now)),
                churn_rate=round(uninstalls / base, 4) if base else None,
            )
        )
        m = _next_month(m)

    lo = datetime.combine(start, time.min, UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, UTC)
    in_range = [e for e in events if lo <= _utc(e.created_at) < hi]
    reasons = Counter(e.reason or "unspecified" for e in in_range)
    paid_active = await db.scalar(
        select(func.count(AppSubscriptionModel.id)).where(
            AppSubscriptionModel.app_id.in_(ids),
            AppSubscriptionModel.status == "active",
        )
    )

    api_from = max(lo, now - timedelta(days=APP_RETENTION_DAYS))
    api_to = min(hi, now)
    counters: dict[str, dict[str, int]] = {}
    if api_from < api_to and ids:
        try:
            counters = await app_hourly([str(i) for i in ids], api_from, api_to)
        except Exception:
            logger.warning("partner_analytics_api_counters_failed")
    api = []
    for aid, name in apps:
        c = counters.get(str(aid), {})
        n, errors = c.get("n", 0), c.get("4xx", 0) + c.get("5xx", 0)
        api.append(
            AppApiHealth(
                app_id=aid,
                app_name=name,
                requests=n,
                errors=errors,
                error_rate=round(errors / n, 4) if n else None,
            )
        )

    return SuccessResponse(
        data=PartnerAnalytics(
            months=months,
            reasons=[ReasonCount(reason=r, count=n) for r, n in reasons.most_common()],
            notes=[
                ReasonNote(
                    app_id=e.app_id,
                    reason=e.reason,
                    text=e.reason_text,
                    created_at=e.created_at,
                )
                for e in in_range
                if e.reason_text
            ][:20],
            paid_active=paid_active or 0,
            trial_to_paid=None,
            trial_note="no_trial_marker",
            api=api,
            api_from=api_from if api_from < api_to else None,
        )
    )


# ─── API logs ─────────────────────────────────────────────────────


class ApiLogOut(BaseModel):
    at: datetime
    request_id: str | None
    method: str
    route: str
    status: int
    latency_ms: float
    rate_limited: bool
    store_id: UUID | None
    store_name: str | None


class ApiLogStats(BaseModel):
    requests: int
    errors: int
    error_rate: float | None
    p95_ms: int | None
    rate_limited: int


class ApiLogPage(BaseModel):
    items: list[ApiLogOut]
    total: int
    page: int
    page_size: int
    stats: ApiLogStats
    routes: list[str]


@router.get(
    "/me/apps/{app_id}/api-logs",
    response_model=SuccessResponse[ApiLogPage],
    operation_id="list_partner_app_api_logs",
)
async def api_logs(
    app_id: UUID,
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_class: Literal["2xx", "3xx", "4xx", "5xx"] | None = None,
    route: str | None = None,
    store_id: UUID | None = None,
    hours: Annotated[int, Query(ge=1, le=APP_RETENTION_DAYS * 24)] = 24,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """Requests the app made with its tokens in the last ``hours``: the list
    holds the latest APP_KEEP, the stats count every request."""
    if await db.scalar(_apps_of(owner_id, app_id)) is None:
        raise HTTPException(status_code=404, detail="App not found")
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    try:
        entries = await app_log_entries(str(app_id))
        c = (await app_hourly([str(app_id)], since, now))[str(app_id)]
    except Exception:
        logger.warning("partner_api_logs_read_failed", app_id=str(app_id))
        raise HTTPException(status_code=503, detail="API logs are unavailable.")
    entries = [e for e in entries if e["t"] >= since.timestamp()]
    routes = sorted({e["r"] for e in entries})
    if status_class:
        entries = [e for e in entries if f"{e['s'] // 100}xx" == status_class]
    if route:
        entries = [e for e in entries if e["r"] == route]
    if store_id:
        entries = [e for e in entries if e.get("st") == str(store_id)]
    rows = entries[(page - 1) * page_size : page * page_size]
    store_ids = {UUID(e["st"]) for e in rows if e.get("st")}
    names = {}
    if store_ids:
        names = {
            str(sid): name
            for sid, name in (
                await db.execute(
                    select(StoreModel.id, StoreModel.name).where(
                        StoreModel.id.in_(store_ids)
                    )
                )
            ).all()
        }
    n, errors = c.get("n", 0), c.get("4xx", 0) + c.get("5xx", 0)
    return SuccessResponse(
        data=ApiLogPage(
            items=[
                ApiLogOut(
                    at=datetime.fromtimestamp(e["t"], UTC),
                    request_id=e.get("id"),
                    method=e["m"],
                    route=e["r"],
                    status=e["s"],
                    latency_ms=e["ms"],
                    rate_limited=e["s"] == 429,
                    store_id=e.get("st"),
                    store_name=names.get(e.get("st")),
                )
                for e in rows
            ],
            total=len(entries),
            page=page,
            page_size=page_size,
            stats=ApiLogStats(
                requests=n,
                errors=errors,
                error_rate=round(errors / n, 4) if n else None,
                p95_ms=p95_from_buckets(c),
                rate_limited=c.get("429", 0),
            ),
            routes=routes,
        )
    )


# ─── Team ─────────────────────────────────────────────────────────


class MemberOut(BaseModel):
    id: UUID | None
    email: str
    name: str | None
    role: str
    status: str
    created_at: datetime


class InviteRequest(BaseModel):
    email: EmailStr
    role: Literal["admin", "developer"]


class RoleRequest(BaseModel):
    role: Literal["admin", "developer"]


def _name(user: UserModel | None) -> str | None:
    if user is None:
        return None
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or None


async def _member(db: AsyncSession, ctx: PartnerContext, member_id: UUID):
    m = await db.get(PartnerMemberModel, member_id)
    if m is None or m.partner_id != ctx.account.id:
        raise HTTPException(status_code=404, detail="Member not found")
    return m


@router.get(
    "/me/team",
    response_model=SuccessResponse[list[MemberOut]],
    operation_id="list_partner_team",
)
async def list_team(
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if ctx.account is None:
        return SuccessResponse(data=[])
    owner = await db.get(UserModel, ctx.account.user_id)
    rows = (
        await db.execute(
            select(PartnerMemberModel, UserModel)
            .outerjoin(UserModel, UserModel.id == PartnerMemberModel.user_id)
            .where(PartnerMemberModel.partner_id == ctx.account.id)
            .order_by(PartnerMemberModel.created_at)
        )
    ).all()
    return SuccessResponse(
        data=[
            MemberOut(
                id=None,
                email=owner.email if owner else "",
                name=_name(owner),
                role="owner",
                status="active",
                created_at=ctx.account.created_at,
            ),
            *(
                MemberOut(
                    id=m.id,
                    email=m.email,
                    name=_name(u),
                    role=m.role,
                    status=m.status,
                    created_at=m.created_at,
                )
                for m, u in rows
            ),
        ]
    )


@router.post(
    "/me/team",
    response_model=SuccessResponse[MemberOut],
    status_code=status.HTTP_201_CREATED,
    operation_id="invite_partner_member",
)
async def invite_member(
    body: InviteRequest,
    ctx: Annotated[PartnerContext, Depends(require_partner_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    email_service: Annotated[object, Depends(get_email_service)],
):
    email = body.email.lower()
    owner = await db.get(UserModel, ctx.account.user_id)
    taken = await db.scalar(
        select(PartnerMemberModel.id).where(
            PartnerMemberModel.partner_id == ctx.account.id,
            func.lower(PartnerMemberModel.email) == email,
        )
    )
    if taken or (owner and owner.email.lower() == email):
        raise HTTPException(
            status_code=409, detail="That person is already on your team."
        )
    member = PartnerMemberModel(
        partner_id=ctx.account.id,
        email=email,
        role=body.role,
        invited_by=ctx.user_id,
        status="invited",
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)
    inviter = await db.get(UserModel, ctx.user_id)
    try:
        await email_service.send_staff_invitation_email(
            email=email,
            invite_url=settings.merchant_hub_url.replace(
                "://merchant.", "://partners.", 1
            ),
            tenant_name=ctx.account.display_name,
            inviter_name=_name(inviter),
            role=body.role,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("partner_invite_email_failed", error=str(exc))
    logger.info(
        "partner_member_invited", partner_id=str(ctx.account.id), role=body.role
    )
    return SuccessResponse(
        data=MemberOut(
            id=member.id,
            email=member.email,
            name=None,
            role=member.role,
            status=member.status,
            created_at=member.created_at,
        ),
        message="Invitation sent",
    )


@router.patch(
    "/me/team/{member_id}",
    response_model=SuccessResponse[dict],
    operation_id="update_partner_member",
)
async def update_member(
    member_id: UUID,
    body: RoleRequest,
    ctx: Annotated[PartnerContext, Depends(require_partner_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    member = await _member(db, ctx, member_id)
    member.role = body.role
    await db.flush()
    return SuccessResponse(data={"id": str(member.id), "role": member.role})


@router.delete(
    "/me/team/{member_id}",
    response_model=SuccessResponse[dict],
    operation_id="remove_partner_member",
)
async def remove_member(
    member_id: UUID,
    ctx: Annotated[PartnerContext, Depends(require_partner_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    member = await _member(db, ctx, member_id)
    await db.delete(member)
    await db.flush()
    logger.info("partner_member_removed", partner_id=str(ctx.account.id))
    return SuccessResponse(data={"id": str(member_id)}, message="Removed")


@router.post(
    "/invitations/{member_id}/accept",
    response_model=SuccessResponse[dict],
    operation_id="accept_partner_invitation",
)
async def accept_invitation(
    member_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Join a partner's team. Only the verified owner of the invited email
    can accept, and only while they work for no other partner."""
    member = await db.get(PartnerMemberModel, member_id)
    user = await db.get(UserModel, user_id)
    if (
        member is None
        or user is None
        or member.status != "invited"
        or member.email.lower() != user.email.lower()
    ):
        raise HTTPException(status_code=404, detail="Invitation not found")
    if user.email_verified_at is None:
        raise HTTPException(status_code=403, detail="Verify your email first.")
    created = member.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if datetime.now(UTC) - created > INVITE_TTL:
        raise HTTPException(status_code=410, detail="This invitation has expired.")
    if await partner_membership(db, user_id) is not None:
        raise HTTPException(
            status_code=409, detail="You already work with a NUMU partner account."
        )
    member.user_id = user_id
    member.status = "active"
    await db.flush()
    logger.info("partner_invitation_accepted", partner_id=str(member.partner_id))
    return SuccessResponse(
        data={"partner_id": str(member.partner_id)}, message="Joined"
    )
