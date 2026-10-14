"""Merchant 360, health alerts and billing controls (SUPER_ADMIN).

URL: /api/v1/admin/merchants

* ``GET  /attention``                       — live merchants that need a call
* ``GET  /{tenant_id}/insights``            — one tenant, everything staff ask about
* ``POST /{tenant_id}/extend-trial``        — push the trial end out (un-expires it)
* ``POST /{tenant_id}/pause-billing``       — stop renewals, dunning and expiry
* ``POST /{tenant_id}/invoices/{id}/refund`` — mark a paid invoice refunded

The insights payload only aggregates what already exists: the store health
score, the COD roll-up, ``api_usage_daily``, WhatsApp template counts,
``billing_invoices``, ``support_cases`` and ``audit_logs``. The wallet and its
ledger stay at ``/admin/wallets/{tenant_id}``; credits go through its
``/adjust``.

Pause is ``tenants.settings["billing_paused"]``. While it is true the renewal
sweep does not select the tenant (no charge, no retry count, no past_due, no
dunning email, no read_only), the trial expiry sweep does not move it to
read_only, and the read-only purge does not delete it. Nothing else changes:
the storefront lock is derived from the lifecycle, so a tenant that is
already read_only stays locked until it pays or its trial is extended, and
the courtesy pre-expiry emails still go out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin, require_admin_2fa
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.routes.admin.support_cases import _SELECT, UNRESOLVED, _to_model
from src.application.services import api_limits
from src.application.services.audit_service import AuditService
from src.application.services.health_score_service import (
    HEALTH_SCORE_WINDOW_DAYS,
    calculate_store_health_score,
)
from src.application.services.merchant_visibility import LIVE_SQL, live
from src.application.services.wallet_service import WalletService
from src.application.services.whatsapp_entitlement import count_messages
from src.core.entities.wallet import WARNING_LEVEL_LOW, WalletStatus
from src.infrastructure.database.models.public.api_usage import ApiUsageDailyModel
from src.infrastructure.database.models.public.billing import BillingInvoiceModel
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.public.wallet import MerchantWalletModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories.analytics_repository import AnalyticsRepository

router = APIRouter()

_STEP_UP = [Depends(require_admin_2fa(max_age_seconds=300))]

WARNING_LABELS = {1: "low", 2: "negative", 3: "below allowance"}


class ExtendTrialIn(BaseModel):
    days: int = Field(ge=1, le=90)
    reason: str = Field(min_length=3, max_length=500)


class PauseBillingIn(BaseModel):
    paused: bool
    reason: str = Field(min_length=3, max_length=500)


class RefundIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _get_tenant(db: AsyncSession, tenant_id: UUID) -> TenantModel:
    tenant = await db.get(TenantModel, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def _audit(db, admin_id, tenant_id, event, resource_type, resource_id, details):
    return AuditService(db).log(
        event_type=event,
        action="update",
        resource_type=resource_type,
        resource_id=str(resource_id),
        user_id=admin_id,
        tenant_id=tenant_id,
        details={**details, "actor_type": "staff"},
    )


# ---------------------------------------------------------------- attention


@router.get("/attention", response_model=SuccessResponse[list[dict]])
async def merchants_needing_attention(
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Live merchants with a reason to call them.

    ponytail: computed on read from four grouped queries; persist into a
    table refreshed by beat only if this gets slow.
    """
    now = datetime.now(UTC)
    live_sql = LIVE_SQL.format(col="o.tenant_id")
    rows: dict[str, dict] = {}

    def add(tenant_id, store_id, store_name, kind, detail):
        row = rows.setdefault(
            str(tenant_id),
            {
                "tenant_id": str(tenant_id),
                "store_id": None,
                "name": None,
                "reasons": [],
            },
        )
        if store_id and not row["store_id"]:
            row["store_id"], row["name"] = str(store_id), store_name
        row["reasons"].append({"kind": kind, "detail": detail})

    returns = await db.execute(
        text(
            f"""
            SELECT o.tenant_id, o.store_id, s.name,
                   count(*) FILTER (WHERE upper(o.status::text) = 'RETURNED') AS returned,
                   count(*) AS shipped
            FROM public.orders o
            JOIN public.stores s ON s.id = o.store_id
            WHERE o.created_at >= :since
              AND upper(o.status::text) IN ('DELIVERED', 'RETURNED')
              AND {live_sql}
            GROUP BY o.tenant_id, o.store_id, s.name
            HAVING count(*) >= 10
               AND count(*) FILTER (WHERE upper(o.status::text) = 'RETURNED') * 4
                   >= count(*)
            """  # nosec B608 - interpolates a module literal only
        ),
        {"since": now - timedelta(days=30)},
    )
    for r in returns:
        add(
            r.tenant_id,
            r.store_id,
            r.name,
            "return_spike",
            f"{r.returned} of {r.shipped} shipped orders returned in 30 days "
            f"({round(100 * r.returned / r.shipped)}%)",
        )

    stuck = await db.execute(
        text(
            f"""
            SELECT o.tenant_id, o.store_id, s.name, count(*) AS n
            FROM public.orders o
            JOIN public.stores s ON s.id = o.store_id
            WHERE upper(o.status::text) IN ('PENDING', 'CONFIRMED', 'PROCESSING')
              AND o.created_at < :older AND o.created_at >= :younger
              AND {live_sql}
            GROUP BY o.tenant_id, o.store_id, s.name
            HAVING count(*) >= 3
            """  # nosec B608 - interpolates a module literal only
        ),
        {"older": now - timedelta(hours=72), "younger": now - timedelta(days=30)},
    )
    for r in stuck:
        add(
            r.tenant_id,
            r.store_id,
            r.name,
            "stuck_orders",
            f"{r.n} orders pending, confirmed or processing for over 72 hours",
        )

    service = WalletService(db)
    await service.load_admin_defaults()
    wallets = (
        await db.execute(
            select(MerchantWalletModel)
            .join(TenantModel, TenantModel.id == MerchantWalletModel.tenant_id)
            .where(
                live(MerchantWalletModel.tenant_id),
                TenantModel.plan == "payg",
                MerchantWalletModel.status == WalletStatus.ACTIVE.value,
            )
        )
    ).scalars()
    for w in wallets:
        level = service.current_warning_level(w)
        if level >= WARNING_LEVEL_LOW:
            add(
                w.tenant_id,
                None,
                None,
                "wallet",
                f"Wallet {WARNING_LABELS[level]}: "
                f"{w.balance_cents / 100:,.2f} {w.currency}",
            )

    trials = await db.execute(
        text(
            f"""
            SELECT t.id, t.expires_at
            FROM public.tenants t
            WHERE t.lifecycle_state = 'trial'
              AND t.trial_converted_at IS NULL
              AND t.expires_at IS NOT NULL AND t.expires_at <= :soon
              AND {LIVE_SQL.format(col="t.id")}
            """  # nosec B608 - interpolates a module literal only
        ),
        {"soon": now + timedelta(days=3)},
    )
    for r in trials:
        add(
            r.id,
            None,
            None,
            "trial_ending",
            f"Trial ends {r.expires_at:%Y-%m-%d %H:%M} UTC",
        )

    if not rows:
        return SuccessResponse(data=[])

    # Name the rows that only have tenant-level reasons after their first store.
    ids = [UUID(t) for t in rows]
    first_store = await db.execute(
        select(StoreModel.tenant_id, StoreModel.id, StoreModel.name, TenantModel.name)
        .join(TenantModel, TenantModel.id == StoreModel.tenant_id)
        .where(StoreModel.tenant_id.in_(ids))
        .order_by(StoreModel.tenant_id, StoreModel.created_at)
    )
    for tenant_id, store_id, store_name, tenant_name in first_store:
        row = rows[str(tenant_id)]
        if not row["store_id"]:
            row["store_id"], row["name"] = str(store_id), store_name
        row["name"] = row["name"] or tenant_name

    return SuccessResponse(data=sorted(rows.values(), key=lambda r: -len(r["reasons"])))


# ---------------------------------------------------------------- insights


@router.get("/{tenant_id}/insights", response_model=SuccessResponse[dict])
async def merchant_insights(
    tenant_id: UUID,
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant = await _get_tenant(db, tenant_id)
    now = datetime.now(UTC)
    stores = (
        await db.execute(
            select(StoreModel.id, StoreModel.name)
            .where(StoreModel.tenant_id == tenant_id)
            .order_by(StoreModel.created_at)
        )
    ).all()

    analytics = AnalyticsRepository(db)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    health, whatsapp = [], []
    for store_id, name in stores:
        score = await calculate_store_health_score(db, store_id, lang="en")
        cod = await analytics.cod_summary(
            store_id, now - timedelta(days=HEALTH_SCORE_WINDOW_DAYS), now
        )
        health.append({
            "store_id": str(store_id),
            "store_name": name,
            "score": score["score"],
            "grade": score["grade"],
            "delivery_success_rate": score["metrics"]["delivery_success_rate"],
            "cod_success_rate": score["metrics"]["cod_acceptance_rate"],
            "return_rate": score["metrics"]["return_rate"],
            "cod_rejection_rate": round(100 * cod["rejected"] / cod["total"], 1)
            if cod["total"]
            else 0.0,
            "cod_orders": cod["total"],
            "orders_analyzed": score["orders_analyzed"],
            "insufficient_data": score["insufficient_data"],
            "window_days": HEALTH_SCORE_WINDOW_DAYS,
        })
        whatsapp.append({
            "store_id": str(store_id),
            "store_name": name,
            "messages_this_month": await count_messages(db, store_id, month_start),
        })

    U = ApiUsageDailyModel
    today = now.date()
    usage = (
        await db.execute(
            select(
                func.coalesce(func.sum(U.requests), 0),
                func.coalesce(func.sum(U.throttled), 0),
                func.coalesce(func.sum(U.errors_5xx), 0),
                func.coalesce(func.sum(case((U.day == today, U.requests), else_=0)), 0),
            ).where(U.tenant_id == tenant_id, U.day > today - timedelta(days=30))
        )
    ).one()
    policy = await api_limits.policy_for_tenant(db, tenant_id)

    invoices = (
        (
            await db.execute(
                select(BillingInvoiceModel)
                .where(BillingInvoiceModel.tenant_id == tenant_id)
                .order_by(BillingInvoiceModel.created_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )

    open_cases = await db.scalar(
        text(
            "SELECT count(*) FROM public.support_cases "
            "WHERE tenant_id = :t AND status = ANY(:s)"
        ),
        {"t": tenant_id, "s": list(UNRESOLVED)},
    )
    cases = (
        await db.execute(
            text(
                f"{_SELECT} WHERE c.tenant_id = :t ORDER BY c.created_at DESC LIMIT 10"  # nosec B608 - module literal
            ),
            {"t": tenant_id},
        )
    ).mappings()

    timeline = (
        await db.execute(
            text(
                """
                SELECT a.id, a.event_type, a.action, a.resource_type, a.resource_id,
                       a.details, a.created_at, u.email AS actor_email
                FROM public.audit_logs a
                LEFT JOIN public.users u ON u.id = a.user_id
                WHERE a.tenant_id = :t
                   OR a.store_id IN (SELECT id FROM public.stores WHERE tenant_id = :t)
                ORDER BY a.created_at DESC
                LIMIT 50
                """
            ),
            {"t": tenant_id},
        )
    ).mappings()

    settings = tenant.settings or {}
    return SuccessResponse(
        data={
            "tenant_id": str(tenant.id),
            "billing": {
                "paused": bool(settings.get("billing_paused")),
                "lifecycle_state": tenant.lifecycle_state,
                "expires_at": _iso(tenant.expires_at),
                "read_only_at": _iso(tenant.read_only_at),
                "delete_at": _iso(tenant.delete_at),
                "next_renewal_at": _iso(tenant.next_renewal_at),
                "trial_converted_at": _iso(tenant.trial_converted_at),
            },
            "health": health,
            "api_usage": {
                "requests_30d": int(usage[0]),
                "throttled_30d": int(usage[1]),
                "errors_5xx_30d": int(usage[2]),
                "requests_today": int(usage[3]),
                "limits": {
                    "allowed": policy.allowed,
                    "per_minute": policy.per_minute,
                    "per_second": policy.per_second,
                    "monthly_quota": policy.monthly_quota,
                    "key_limit": policy.key_limit,
                },
            },
            "whatsapp": whatsapp,
            "invoices": [
                {
                    "id": str(i.id),
                    "period_start": _iso(i.period_start),
                    "period_end": _iso(i.period_end),
                    "amount_cents": i.amount_cents,
                    "currency": i.currency,
                    "status": i.status,
                    "paid_at": _iso(i.paid_at),
                    "created_at": _iso(i.created_at),
                }
                for i in invoices
            ],
            "support_cases": {
                "open": int(open_cases or 0),
                "recent": [_to_model(r).model_dump(mode="json") for r in cases],
            },
            "timeline": [
                {
                    "id": str(r["id"]),
                    "event_type": r["event_type"],
                    "action": r["action"],
                    "resource_type": r["resource_type"],
                    "resource_id": r["resource_id"],
                    "details": r["details"],
                    "actor_email": r["actor_email"],
                    "created_at": _iso(r["created_at"]),
                }
                for r in timeline
            ],
        }
    )


# ---------------------------------------------------------------- billing


@router.post(
    "/{tenant_id}/extend-trial",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def extend_trial(
    tenant_id: UUID,
    body: ExtendTrialIn,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Move the trial end ``days`` past max(now, expires_at).

    A tenant the trial expiry sweep already moved to read_only is put back
    into the trial: that sweep set read_only_at and delete_at and cleared
    expires_at, so the inverse clears both and sets expires_at again. Such a
    tenant is told apart from a cancelled or dunned one by never having
    converted (``trial_converted_at`` is set by every activation path).
    """
    tenant = await _get_tenant(db, tenant_id)
    expired_trial = tenant.is_read_only and tenant.trial_converted_at is None
    if not (tenant.is_on_trial or expired_trial):
        raise HTTPException(
            status_code=409,
            detail="Only a trial, or a trial that expired into read-only, can be extended",
        )

    old = {
        "lifecycle_state": tenant.lifecycle_state,
        "expires_at": _iso(tenant.expires_at),
        "delete_at": _iso(tenant.delete_at),
    }
    now = datetime.now(UTC)
    start = max(now, tenant.expires_at) if tenant.expires_at else now
    tenant.expires_at = start + timedelta(days=body.days)
    if expired_trial:
        tenant.lifecycle_state = TenantLifecycleState.TRIAL.value
        tenant.read_only_at = None
        tenant.delete_at = None

    await _audit(
        db,
        admin_id,
        tenant_id,
        "billing.trial_extended",
        "tenant",
        tenant_id,
        {
            "reason": body.reason,
            "days": body.days,
            "old_value": old,
            "new_value": {
                "lifecycle_state": tenant.lifecycle_state,
                "expires_at": _iso(tenant.expires_at),
            },
        },
    )
    await db.commit()
    # The checkout gate exempts a live trial by lifecycle and expires_at.
    await WalletService(db).invalidate_cache(tenant_id)
    return SuccessResponse(
        data={
            "tenant_id": str(tenant_id),
            "lifecycle_state": tenant.lifecycle_state,
            "expires_at": _iso(tenant.expires_at),
        },
        message=f"Trial extended by {body.days} days",
    )


@router.post(
    "/{tenant_id}/pause-billing",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def pause_billing(
    tenant_id: UUID,
    body: PauseBillingIn,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Set or clear ``settings.billing_paused`` (see the module docstring)."""
    tenant = await _get_tenant(db, tenant_id)
    was = bool((tenant.settings or {}).get("billing_paused"))
    # Reassigned, not mutated in place: JSONB changes are not tracked.
    tenant.settings = {**(tenant.settings or {}), "billing_paused": body.paused}
    await _audit(
        db,
        admin_id,
        tenant_id,
        "billing.paused" if body.paused else "billing.resumed",
        "tenant",
        tenant_id,
        {"reason": body.reason, "old_value": was, "new_value": body.paused},
    )
    await db.commit()
    return SuccessResponse(
        data={"tenant_id": str(tenant_id), "paused": body.paused},
        message="Billing paused: no renewal charges, dunning, trial expiry or purge"
        if body.paused
        else "Billing resumed",
    )


@router.post(
    "/{tenant_id}/invoices/{invoice_id}/refund",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def refund_invoice(
    tenant_id: UUID,
    invoice_id: UUID,
    body: RefundIn,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    invoice = (
        await db.execute(
            select(BillingInvoiceModel).where(
                BillingInvoiceModel.id == invoice_id,
                BillingInvoiceModel.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != "paid":
        raise HTTPException(
            status_code=409,
            detail=f"Only a paid invoice can be refunded ({invoice.status})",
        )
    invoice.status = "refunded"
    await _audit(
        db,
        admin_id,
        tenant_id,
        "billing.invoice_refunded",
        "billing_invoice",
        invoice_id,
        {
            "reason": body.reason,
            "amount_cents": invoice.amount_cents,
            "currency": invoice.currency,
        },
    )
    await db.commit()
    return SuccessResponse(
        data={"invoice_id": str(invoice_id), "status": invoice.status},
        message="Invoice marked refunded. No money moved: return "
        f"{invoice.amount_cents / 100:,.2f} {invoice.currency} to the merchant "
        "out of band (InstaPay).",
    )
