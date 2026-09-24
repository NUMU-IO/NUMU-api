"""Partner portal (partners.numueg.app): dashboard, webhook deliveries, team,
subscriptions, app coupons and referrals.

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
from pydantic import BaseModel, EmailStr, Field, model_validator
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
from src.api.responses import SuccessResponse
from src.application.services.app_billing import (
    COUPON_CODE_RE,
    app_price,
    coupon_discount,
    coupon_out,
    effective_share_bps,
    normalize_code,
    partner_balance,
    partner_payable,
    quote,
    redemption_count,
)
from src.application.services.partner_program import partner_membership
from src.application.services.partner_referrals import (
    SIGNUP_LINK,
    ensure_code,
    referred_stores,
)
from src.config import settings
from src.core.entities.webhook import WebhookDeliveryStatus
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppUninstallEventModel,
)
from src.infrastructure.database.models.public.app_billing import (
    AppCouponModel,
    AppCouponRedemptionModel,
    AppSubscriptionModel,
    PartnerLedgerEntryModel,
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
    net_sales_cents: int = 0
    balance_cents: int = 0
    payable_cents: int = 0


@router.get(
    "/me/dashboard",
    response_model=SuccessResponse[PartnerDashboard],
    operation_id="get_partner_dashboard",
)
async def dashboard(
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    app_id: UUID | None = None,
):
    apps = _apps_of(ctx.owner_id, app_id)
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
    money = {}
    if ctx.account is not None:
        net_sales = await db.scalar(
            select(
                func.coalesce(func.sum(PartnerLedgerEntryModel.amount_cents), 0)
            ).where(
                PartnerLedgerEntryModel.partner_id == ctx.account.id,
                PartnerLedgerEntryModel.kind == "sale",
                PartnerLedgerEntryModel.app_id.in_(apps),
                *_in_range(PartnerLedgerEntryModel.created_at, start, end),
            )
        )
        money = {
            "net_sales_cents": int(net_sales or 0),
            "balance_cents": await partner_balance(db, ctx.account.id),
            "payable_cents": await partner_payable(db, ctx.account.id),
        }
    return SuccessResponse(
        data=PartnerDashboard(
            **money,
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


# ─── Subscriptions ────────────────────────────────────────────────


def _sub_status(sub: AppSubscriptionModel) -> str:
    return "trial" if sub.status == "active" and sub.is_trial else sub.status


@router.get(
    "/me/subscriptions",
    response_model=SuccessResponse[dict],
    operation_id="list_partner_subscriptions",
)
async def list_subscriptions(
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    app_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Stores subscribed to your paid apps: counts by status (``trial`` is
    an active subscription in its free trial) and the latest subscriptions."""
    rows = (
        await db.execute(
            select(AppSubscriptionModel, AppModel.name, StoreModel.name)
            .join(AppModel, AppModel.id == AppSubscriptionModel.app_id)
            .outerjoin(StoreModel, StoreModel.id == AppSubscriptionModel.store_id)
            .where(AppSubscriptionModel.app_id.in_(_apps_of(owner_id, app_id)))
            .order_by(AppSubscriptionModel.created_at.desc())
        )
    ).all()
    counts = Counter(_sub_status(sub) for sub, _, _ in rows)
    return SuccessResponse(
        data={
            "counts": {
                k: counts[k] for k in ("active", "trial", "past_due", "cancelled")
            },
            "total": len(rows),
            "items": [
                {
                    "id": str(sub.id),
                    "app_id": str(sub.app_id),
                    "app_name": app_name,
                    "store_name": store_name,
                    "status": _sub_status(sub),
                    "price_cents": sub.price_cents,
                    "currency": sub.currency,
                    "cycle": sub.cycle,
                    "current_period_end": sub.current_period_end,
                    "cancel_at_period_end": sub.cancel_at_period_end,
                    "created_at": sub.created_at,
                }
                for sub, app_name, store_name in rows[:limit]
            ],
        }
    )


# ─── Coupons ──────────────────────────────────────────────────────


class AppCouponCreate(BaseModel):
    app_id: UUID
    code: str = Field(min_length=3, max_length=40)
    percent_off: int | None = Field(default=None, ge=1, le=100)
    amount_off_cents: int | None = Field(default=None, gt=0)
    duration_cycles: int | None = Field(default=None, ge=1, le=120)
    max_redemptions: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    store_id: UUID | None = None

    @model_validator(mode="after")
    def _check(self):
        self.code = normalize_code(self.code)
        if not COUPON_CODE_RE.match(self.code):
            raise ValueError("code: letters, digits, - and _ only")
        if (self.percent_off is None) == (self.amount_off_cents is None):
            raise ValueError("Set percent_off or amount_off_cents, not both.")
        return self


class AppCouponUpdate(BaseModel):
    active: bool


def _cap(c: AppCouponModel, app: AppModel, share_bps: int) -> dict:
    """What the coupon takes off the app's current price, capped at your
    share: NUMU's fee and its VAT are always on the full price."""
    price = app_price(app)
    list_cents = price.price_cents if price else 0
    q = quote(list_cents, share_bps, coupon_discount(c, list_cents))
    return {
        "list_price_cents": list_cents,
        "discount_cents": q.discount_cents,
        "max_discount_cents": list_cents - q.fee_cents,
        "capped": q.capped,
    }


async def _coupon(db: AsyncSession, ctx: PartnerContext, coupon_id: UUID):
    c = await db.get(AppCouponModel, coupon_id)
    if c is None or ctx.account is None or c.partner_id != ctx.account.id:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return c


@router.get(
    "/me/coupons",
    response_model=SuccessResponse[list[dict]],
    operation_id="list_partner_coupons",
)
async def list_coupons(
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if ctx.account is None:
        return SuccessResponse(data=[])
    share = effective_share_bps(ctx.account)
    rows = await db.execute(
        select(AppCouponModel, AppModel, redemption_count())
        .join(AppModel, AppModel.id == AppCouponModel.app_id)
        .where(AppCouponModel.partner_id == ctx.account.id)
        .order_by(AppCouponModel.created_at.desc())
    )
    return SuccessResponse(
        data=[
            {**coupon_out(c, app.name, used or 0), **_cap(c, app, share)}
            for c, app, used in rows
        ]
    )


@router.post(
    "/me/coupons",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
    operation_id="create_partner_coupon",
)
async def create_coupon(
    body: AppCouponCreate,
    ctx: Annotated[PartnerContext, Depends(require_partner_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """A discount code for one of your paid apps. You fund it: it comes out
    of your share, never NUMU's fee, and a discount larger than your share
    is capped at it (``capped``)."""
    app = await db.get(AppModel, body.app_id)
    if app is None or app.developer_id != ctx.owner_id:
        raise HTTPException(status_code=404, detail="App not found")
    price = app_price(app)
    if price is None or not price.price_cents:
        raise HTTPException(
            status_code=422, detail={"code": "coupon_app_not_recurring"}
        )
    exists = await db.scalar(
        select(AppCouponModel.id).where(
            AppCouponModel.app_id == app.id, AppCouponModel.code == body.code
        )
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail={"code": "coupon_code_taken"})
    c = AppCouponModel(partner_id=ctx.account.id, **body.model_dump())
    db.add(c)
    await db.flush()
    return SuccessResponse(
        data={
            **coupon_out(c, app.name, 0),
            **_cap(c, app, effective_share_bps(ctx.account)),
        }
    )


@router.patch(
    "/me/coupons/{coupon_id}",
    response_model=SuccessResponse[dict],
    operation_id="update_partner_coupon",
)
async def update_coupon(
    coupon_id: UUID,
    body: AppCouponUpdate,
    ctx: Annotated[PartnerContext, Depends(require_partner_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Disable (or re-enable) a coupon. Stores that already redeemed it keep
    their discount for the periods it covers."""
    c = await _coupon(db, ctx, coupon_id)
    c.active = body.active
    await db.flush()
    app = await db.get(AppModel, c.app_id)
    used = await db.scalar(
        select(func.count(AppCouponRedemptionModel.id)).where(
            AppCouponRedemptionModel.coupon_id == c.id
        )
    )
    return SuccessResponse(data=coupon_out(c, app.name if app else None, used or 0))


@router.get(
    "/me/coupons/{coupon_id}/redemptions",
    response_model=SuccessResponse[list[dict]],
    operation_id="list_partner_coupon_redemptions",
)
async def list_redemptions(
    coupon_id: UUID,
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    c = await _coupon(db, ctx, coupon_id)
    rows = await db.execute(
        select(AppCouponRedemptionModel, StoreModel.name)
        .outerjoin(StoreModel, StoreModel.id == AppCouponRedemptionModel.store_id)
        .where(AppCouponRedemptionModel.coupon_id == c.id)
        .order_by(AppCouponRedemptionModel.created_at.desc())
    )
    return SuccessResponse(
        data=[
            {
                "id": str(r.id),
                "store_id": str(r.store_id),
                "store_name": store_name,
                "created_at": r.created_at,
            }
            for r, store_name in rows
        ]
    )


# ─── Referrals ────────────────────────────────────────────────────


class ReferredStore(BaseModel):
    tenant_id: UUID
    store_name: str
    signed_up_at: datetime
    plan: str
    status: str
    first_paid_at: datetime | None
    earned_cents: int


class PartnerReferrals(BaseModel):
    code: str | None
    link: str | None
    referral_bps: int
    referral_months: int
    earned_cents: int
    stores: list[ReferredStore]


@router.get(
    "/me/referrals",
    response_model=SuccessResponse[PartnerReferrals],
    operation_id="get_partner_referrals",
)
async def referrals(
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Your referral link and the merchants it brought: you earn a share of
    each one's NUMU plan payments for a set period after their first."""
    if ctx.account is None:
        return SuccessResponse(
            data=PartnerReferrals(
                code=None,
                link=None,
                referral_bps=0,
                referral_months=0,
                earned_cents=0,
                stores=[],
            )
        )
    code = await ensure_code(db, ctx.account)
    stores = [ReferredStore(**r) for r in await referred_stores(db, ctx.account.id)]
    return SuccessResponse(
        data=PartnerReferrals(
            code=code,
            link=SIGNUP_LINK + code,
            referral_bps=ctx.account.referral_bps,
            referral_months=ctx.account.referral_months,
            earned_cents=sum(r.earned_cents for r in stores),
            stores=stores,
        )
    )
