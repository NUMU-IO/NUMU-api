"""Admin: the entitlement catalog, merchant overrides and release flags.

URL: /api/v1/admin/entitlements

Every write needs a reason and lands in audit_logs in the same transaction.
Catalog-wide writes (kill switch, plan grants, flags) rotate the catalog token
after their commit; per-tenant writes (overrides, flag targets) bump the
tenant's version inside theirs. What these screens show is computed straight
from the database, never read from the cache.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin, require_admin_2fa
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.audit_service import AuditService
from src.application.services.entitlement_service import (
    EntitlementService,
    aware,
    plan_grants,
)
from src.core.entities.plan import PLAN_LIMITS
from src.core.entitlements import Flag, FlagTarget, bucket, check_value, flag_on
from src.infrastructure.database.models.audit import AuditLogModel
from src.infrastructure.database.models.public.app import AppModel
from src.infrastructure.database.models.public.app_billing import (
    AppSubscriptionModel,
)
from src.infrastructure.database.models.public.entitlements import (
    EntitlementOverrideModel,
    FeatureFlagModel,
    FeatureFlagTargetModel,
    FeatureModel,
    PlanEntitlementModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.tenancy.repository import TenantRepository

router = APIRouter()

Admin = Annotated[UUID, Depends(require_admin)]
Db = Annotated[AsyncSession, Depends(get_db)]
_STEP_UP = [Depends(require_admin_2fa(max_age_seconds=300))]
#: Only a named contract may run longer than a year, or forever.
MAX_OVERRIDE = timedelta(days=366)
#: Matrix column order; other plan keys sort after these, add-ons last.
PLAN_ORDER = (
    "demo",
    "trial",
    "free",
    "beta",
    "payg",
    "starter",
    "pro",
    "developer",
    "enterprise",
)
Reason = Annotated[str, Field(min_length=3, max_length=500)]


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _plan_columns(grants: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        set(grants) | set(PLAN_LIMITS),
        key=lambda k: (
            k.startswith("addon:"),
            PLAN_ORDER.index(k) if k in PLAN_ORDER else len(PLAN_ORDER),
            k,
        ),
    )


def _feature_out(row: FeatureModel) -> dict[str, Any]:
    return {
        "key": row.key,
        "name": row.name,
        "name_ar": row.name_ar,
        "description": row.description,
        "category": row.category,
        "kind": row.kind,
        "default_value": row.default_value,
        "usage": row.usage,
        "period": row.period,
        "enforcement": row.enforcement,
        "unit": row.unit,
        "is_enabled": row.is_enabled,
        "disabled_reason": row.disabled_reason,
    }


def _override_status(row: EntitlementOverrideModel, now: datetime) -> str:
    if row.revoked_at is not None:
        return "revoked"
    if aware(row.starts_at) > now:
        return "scheduled"
    if row.expires_at is not None and aware(row.expires_at) <= now:
        return "expired"
    return "live"


async def _emails(db: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = await db.execute(
        select(UserModel.id, UserModel.email).where(UserModel.id.in_(wanted))
    )
    return dict(rows.all())


def _override_out(
    row: EntitlementOverrideModel, now: datetime, emails: dict[UUID, str]
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "feature_key": row.feature_key,
        "value": row.value,
        "source": row.source,
        "reason": row.reason,
        "starts_at": _iso(row.starts_at),
        "expires_at": _iso(row.expires_at),
        "created_by": emails.get(row.created_by),
        "created_at": _iso(row.created_at),
        "revoked_at": _iso(row.revoked_at),
        "revoked_by": emails.get(row.revoked_by),
        "status": _override_status(row, now),
    }


async def _feature_or_404(
    db: AsyncSession, key: str, *, lock: bool = False
) -> FeatureModel:
    stmt = select(FeatureModel).where(FeatureModel.key == key)
    feature = await db.scalar(stmt.with_for_update() if lock else stmt)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"Unknown feature '{key}'")
    return feature


async def _tenant_or_404(db: AsyncSession, tenant_id: UUID) -> TenantModel:
    tenant = await db.get(TenantModel, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


async def _flag_or_404(db: AsyncSession, key: str) -> FeatureFlagModel:
    flag = await db.get(FeatureFlagModel, key)
    if flag is None:
        raise HTTPException(status_code=404, detail=f"Unknown flag '{key}'")
    return flag


async def _affected_tenants(db: AsyncSession, plan_key: str) -> int:
    """Tenants a plan-grant change reaches: those on the plan, or holding a
    live subscription to the add-on's app."""
    if plan_key.startswith("addon:"):
        return await db.scalar(
            select(func.count(func.distinct(AppSubscriptionModel.tenant_id)))
            .join(AppModel, AppModel.id == AppSubscriptionModel.app_id)
            .where(
                AppModel.slug == plan_key.removeprefix("addon:"),
                AppSubscriptionModel.status.in_(("active", "past_due")),
            )
        )
    return await db.scalar(
        select(func.count())
        .select_from(TenantModel)
        .where(TenantModel.plan == plan_key)
    )


# ─── Catalog and plan grants ──────────────────────────────────────────────


@router.get("/features", response_model=SuccessResponse[dict])
async def list_features(_admin: Admin, db: Db) -> SuccessResponse[dict]:
    """Every feature with its grant per plan and add-on: the plan matrix."""
    grants = await plan_grants(db)
    now = datetime.now(UTC)
    live = dict(
        (
            await db.execute(
                select(EntitlementOverrideModel.feature_key, func.count())
                .where(
                    EntitlementOverrideModel.revoked_at.is_(None),
                    EntitlementOverrideModel.starts_at <= now,
                    or_(
                        EntitlementOverrideModel.expires_at.is_(None),
                        EntitlementOverrideModel.expires_at > now,
                    ),
                )
                .group_by(EntitlementOverrideModel.feature_key)
            )
        ).all()
    )
    releases: dict[str, list[str]] = {}
    for flag in await db.scalars(
        select(FeatureFlagModel).where(FeatureFlagModel.feature_key.isnot(None))
    ):
        releases.setdefault(flag.feature_key, []).append(flag.key)
    tenant_counts = dict(
        (
            await db.execute(
                select(TenantModel.plan, func.count()).group_by(TenantModel.plan)
            )
        ).all()
    )
    features = await db.scalars(
        select(FeatureModel).order_by(FeatureModel.category, FeatureModel.key)
    )
    return SuccessResponse(
        data={
            "plans": _plan_columns(grants),
            "tenant_counts": tenant_counts,
            "features": [
                _feature_out(f)
                | {
                    "grants": {p: g[f.key] for p, g in grants.items() if f.key in g},
                    "live_overrides": live.get(f.key, 0),
                    "releases": releases.get(f.key, []),
                }
                for f in features
            ],
        }
    )


class FeaturePatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    name_ar: str | None = Field(None, max_length=120)
    description: str | None = None
    enforcement: Literal["hard", "soft"] | None = None
    reason: Reason


@router.patch("/features/{key}", response_model=SuccessResponse[dict])
async def update_feature(
    key: str, body: FeaturePatch, admin_id: Admin, db: Db
) -> SuccessResponse[dict]:
    """Names, description and hard/soft enforcement. Kind, meter and period
    are code: a migration changes them, not this screen."""
    feature = await _feature_or_404(db, key, lock=True)
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    old = {field: getattr(feature, field) for field in changes}
    for field, value in changes.items():
        setattr(feature, field, value)
    await AuditService(db).log(
        event_type="entitlement.feature.update",
        action="update",
        resource_type="feature",
        resource_id=key,
        user_id=admin_id,
        old_value=old,
        new_value=changes,
        details={"reason": body.reason},
    )
    await db.commit()
    await EntitlementService.bump_catalog()
    return SuccessResponse(data=_feature_out(feature))


class KillSwitchIn(BaseModel):
    enabled: bool
    reason: Reason


@router.post(
    "/features/{key}/kill-switch",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def set_kill_switch(
    key: str, body: KillSwitchIn, admin_id: Admin, db: Db
) -> SuccessResponse[dict]:
    """Switch a feature off for every merchant (incident), or back on."""
    feature = await _feature_or_404(db, key, lock=True)
    old = feature.is_enabled
    feature.is_enabled = body.enabled
    feature.disabled_reason = None if body.enabled else body.reason.strip()
    await AuditService(db).log(
        event_type="entitlement.feature.kill_switch",
        action="enable" if body.enabled else "disable",
        resource_type="feature",
        resource_id=key,
        user_id=admin_id,
        severity="warning",
        old_value={"is_enabled": old},
        new_value={"is_enabled": body.enabled},
        details={"reason": body.reason},
    )
    await db.commit()
    await EntitlementService.bump_catalog()
    return SuccessResponse(data=_feature_out(feature))


class GrantIn(BaseModel):
    value: bool | int | Literal["unlimited"]
    reason: Reason


@router.put(
    "/features/{key}/grants/{plan_key}",
    response_model=SuccessResponse[dict],
    dependencies=_STEP_UP,
)
async def set_grant(
    key: str, plan_key: str, body: GrantIn, admin_id: Admin, db: Db
) -> SuccessResponse[dict]:
    """What one plan (or ``addon:<app slug>``) grants for one feature."""
    feature = await _feature_or_404(db, key)
    grants = await plan_grants(db)
    if not (
        plan_key in PLAN_LIMITS or plan_key in grants or plan_key.startswith("addon:")
    ):
        raise HTTPException(status_code=422, detail=f"Unknown plan '{plan_key}'")
    try:
        value = check_value(feature.kind, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    old = grants.get(plan_key, {}).get(key)
    insert = pg_insert(PlanEntitlementModel).values(
        plan_key=plan_key, feature_key=key, value=value, updated_by=admin_id
    )
    await db.execute(
        insert.on_conflict_do_update(
            index_elements=["plan_key", "feature_key"],
            set_={"value": value, "updated_by": admin_id, "updated_at": func.now()},
        )
    )
    affected = await _affected_tenants(db, plan_key)
    await AuditService(db).log(
        event_type="entitlement.plan_grant.update",
        action="update",
        resource_type="feature",
        resource_id=key,
        user_id=admin_id,
        old_value={"plan_key": plan_key, "value": old},
        new_value={"plan_key": plan_key, "value": value},
        details={"reason": body.reason, "affected_tenants": affected},
    )
    await db.commit()
    await EntitlementService.bump_catalog()
    return SuccessResponse(
        data={
            "plan_key": plan_key,
            "feature_key": key,
            "value": value,
            "affected_tenants": affected,
        }
    )


# ─── Merchant overrides ───────────────────────────────────────────────────


@router.get("/features/{key}/overrides", response_model=SuccessResponse[list])
async def list_feature_overrides(
    key: str,
    _admin: Admin,
    db: Db,
    status: Literal["live", "all"] = "live",
) -> SuccessResponse[list]:
    now = datetime.now(UTC)
    stmt = (
        select(
            EntitlementOverrideModel,
            TenantModel.name,
            TenantModel.subdomain,
            TenantModel.plan,
        )
        .join(TenantModel, TenantModel.id == EntitlementOverrideModel.tenant_id)
        .where(EntitlementOverrideModel.feature_key == key)
        .order_by(EntitlementOverrideModel.created_at.desc())
        .limit(500)
    )
    if status == "live":
        stmt = stmt.where(
            EntitlementOverrideModel.revoked_at.is_(None),
            or_(
                EntitlementOverrideModel.expires_at.is_(None),
                EntitlementOverrideModel.expires_at > now,
            ),
        )
    rows = (await db.execute(stmt)).all()
    emails = await _emails(
        db, {r[0].created_by for r in rows} | {r[0].revoked_by for r in rows}
    )
    return SuccessResponse(
        data=[
            _override_out(row, now, emails)
            | {"tenant": {"name": name, "subdomain": subdomain, "plan": plan}}
            for row, name, subdomain, plan in rows
        ]
    )


class OverrideIn(BaseModel):
    feature_key: str
    value: bool | int | Literal["unlimited"]
    source: Literal["support", "sales", "promotion", "beta", "contract", "testing"]
    reason: Reason
    starts_at: datetime | None = None
    expires_at: datetime | None = None


@router.post(
    "/tenants/{tenant_id}/overrides",
    response_model=SuccessResponse[dict],
    status_code=201,
)
async def create_override(
    tenant_id: UUID, body: OverrideIn, admin_id: Admin, db: Db
) -> SuccessResponse[dict]:
    """Give or take one feature from one merchant, without touching the plan.
    Supersedes the merchant's current override for that feature."""
    feature = await _feature_or_404(db, body.feature_key)
    tenant = await _tenant_or_404(db, tenant_id)
    try:
        value = check_value(feature.kind, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now(UTC)
    starts = body.starts_at or now
    if body.expires_at is not None and body.expires_at <= starts:
        raise HTTPException(
            status_code=422, detail="expires_at must be after starts_at"
        )
    if body.source != "contract" and (
        body.expires_at is None or body.expires_at - starts > MAX_OVERRIDE
    ):
        raise HTTPException(
            status_code=422,
            detail="Only a contract override may run longer than a year or forever",
        )

    previous = await db.scalar(
        select(EntitlementOverrideModel)
        .where(
            EntitlementOverrideModel.tenant_id == tenant_id,
            EntitlementOverrideModel.feature_key == feature.key,
            EntitlementOverrideModel.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if previous is not None:
        previous.revoked_at, previous.revoked_by = now, admin_id
    row = EntitlementOverrideModel(
        tenant_id=tenant_id,
        feature_key=feature.key,
        value=value,
        starts_at=starts,
        expires_at=body.expires_at,
        source=body.source,
        reason=body.reason.strip(),
        created_by=admin_id,
    )
    db.add(row)
    await TenantRepository(db).bump_entitlements_version(tenant_id)
    await AuditService(db).log(
        event_type="entitlement.override.create",
        action="create",
        resource_type="feature",
        resource_id=feature.key,
        tenant_id=tenant_id,
        user_id=admin_id,
        old_value=previous
        and {
            "id": str(previous.id),
            "value": previous.value,
            "expires_at": _iso(previous.expires_at),
        },
        new_value={
            "value": value,
            "source": body.source,
            "starts_at": starts.isoformat(),
            "expires_at": _iso(body.expires_at),
        },
        details={"reason": body.reason},
    )
    try:
        await db.flush()
    except IntegrityError as exc:  # uq_entitlement_overrides_live
        raise HTTPException(
            status_code=409, detail="This override changed a moment ago. Reload."
        ) from exc
    await db.refresh(tenant)
    return SuccessResponse(
        data=_override_out(row, now, await _emails(db, {admin_id}))
        | {"explain": await EntitlementService(db).explain(tenant, feature.key)}
    )


class RevokeIn(BaseModel):
    reason: Reason


@router.post("/overrides/{override_id}/revoke", response_model=SuccessResponse[dict])
async def revoke_override(
    override_id: UUID, body: RevokeIn, admin_id: Admin, db: Db
) -> SuccessResponse[dict]:
    row = await db.scalar(
        select(EntitlementOverrideModel)
        .where(EntitlementOverrideModel.id == override_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Override not found")
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Override already revoked")
    now = datetime.now(UTC)
    row.revoked_at, row.revoked_by = now, admin_id
    await TenantRepository(db).bump_entitlements_version(row.tenant_id)
    await AuditService(db).log(
        event_type="entitlement.override.revoke",
        action="revoke",
        resource_type="feature",
        resource_id=row.feature_key,
        tenant_id=row.tenant_id,
        user_id=admin_id,
        old_value={"id": str(row.id), "value": row.value},
        details={"reason": body.reason},
    )
    emails = await _emails(db, {row.created_by, admin_id})
    return SuccessResponse(data=_override_out(row, now, emails))


# ─── One merchant: why does X have Y? ─────────────────────────────────────


@router.get("/tenants", response_model=SuccessResponse[list])
async def search_tenants(
    _admin: Admin,
    db: Db,
    search: Annotated[str, Query(min_length=2, max_length=100)],
) -> SuccessResponse[list]:
    like = f"%{search.strip().lower()}%"
    rows = await db.scalars(
        select(TenantModel)
        .where(
            or_(
                func.lower(TenantModel.name).like(like),
                func.lower(TenantModel.subdomain).like(like),
            )
        )
        .order_by(TenantModel.name)
        .limit(20)
    )
    return SuccessResponse(
        data=[
            {"id": str(t.id), "name": t.name, "subdomain": t.subdomain, "plan": t.plan}
            for t in rows
        ]
    )


@router.get("/tenants/{tenant_id}", response_model=SuccessResponse[dict])
async def tenant_entitlements(
    tenant_id: UUID, _admin: Admin, db: Db
) -> SuccessResponse[dict]:
    """Every feature, flag and meter for one merchant, fresh from the DB."""
    tenant = await _tenant_or_404(db, tenant_id)
    ents = EntitlementService(db)
    snap = await ents.compute(tenant)
    names = {f.key: f.name for f in await db.scalars(select(FeatureModel))}
    now = datetime.now(UTC)
    overrides = list(
        await db.scalars(
            select(EntitlementOverrideModel)
            .where(EntitlementOverrideModel.tenant_id == tenant_id)
            .order_by(EntitlementOverrideModel.created_at.desc())
            .limit(200)
        )
    )
    emails = await _emails(
        db, {o.created_by for o in overrides} | {o.revoked_by for o in overrides}
    )
    return SuccessResponse(
        data={
            "tenant": {
                "id": str(tenant.id),
                "name": tenant.name,
                "subdomain": tenant.subdomain,
                "plan": tenant.plan,
                "entitlements_version": tenant.entitlements_version,
            },
            "features": [
                {"key": key, "name": names.get(key, key)} | state
                for key, state in snap["features"].items()
            ],
            "flags": await ents.explain_flags(tenant),
            "usage": [
                await ents.usage(tenant, key)
                for key, state in snap["features"].items()
                if state.get("usage")
            ],
            "overrides": [_override_out(o, now, emails) for o in overrides],
        }
    )


@router.get("/tenants/{tenant_id}/explain/{key}", response_model=SuccessResponse[dict])
async def explain_feature(
    tenant_id: UUID, key: str, _admin: Admin, db: Db
) -> SuccessResponse[dict]:
    """Every layer that decided this merchant's answer for one feature."""
    tenant = await _tenant_or_404(db, tenant_id)
    await _feature_or_404(db, key)
    return SuccessResponse(data=await EntitlementService(db).explain(tenant, key))


# ─── Release flags ────────────────────────────────────────────────────────


def _flag_out(flag: FeatureFlagModel, targets: int, now: datetime) -> dict[str, Any]:
    return {
        "key": flag.key,
        "description": flag.description,
        "owner": flag.owner,
        "feature_key": flag.feature_key,
        "enabled": flag.enabled,
        "rollout_percent": flag.rollout_percent,
        "targets": targets,
        "created_at": _iso(flag.created_at),
        "updated_at": _iso(flag.updated_at),
        "age_days": (now - aware(flag.created_at)).days if flag.created_at else None,
    }


@router.get("/flags", response_model=SuccessResponse[list])
async def list_flags(_admin: Admin, db: Db) -> SuccessResponse[list]:
    counts = dict(
        (
            await db.execute(
                select(FeatureFlagTargetModel.flag_key, func.count()).group_by(
                    FeatureFlagTargetModel.flag_key
                )
            )
        ).all()
    )
    now = datetime.now(UTC)
    flags = await db.scalars(select(FeatureFlagModel).order_by(FeatureFlagModel.key))
    return SuccessResponse(
        data=[_flag_out(f, counts.get(f.key, 0), now) for f in flags]
    )


class FlagIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    description: str = Field(min_length=3)
    owner: str | None = Field(None, max_length=120)
    feature_key: str | None = None


@router.post("/flags", response_model=SuccessResponse[dict], status_code=201)
async def create_flag(body: FlagIn, admin_id: Admin, db: Db) -> SuccessResponse[dict]:
    """A new release flag, off for everyone until someone turns it on."""
    if body.feature_key:
        await _feature_or_404(db, body.feature_key)
    if await db.get(FeatureFlagModel, body.key) is not None:
        raise HTTPException(status_code=409, detail=f"Flag '{body.key}' exists")
    flag = FeatureFlagModel(
        key=body.key,
        description=body.description,
        owner=body.owner,
        feature_key=body.feature_key,
        enabled=False,
        rollout_percent=0,
    )
    db.add(flag)
    await AuditService(db).log(
        event_type="flag.create",
        action="create",
        resource_type="flag",
        resource_id=body.key,
        user_id=admin_id,
        new_value=body.model_dump(),
    )
    await db.commit()
    await EntitlementService.bump_catalog()
    return SuccessResponse(data=_flag_out(flag, 0, datetime.now(UTC)))


class FlagPatch(BaseModel):
    enabled: bool | None = None
    rollout_percent: int | None = Field(None, ge=0, le=100)
    description: str | None = None
    owner: str | None = Field(None, max_length=120)
    feature_key: str | None = None
    reason: Reason


@router.patch(
    "/flags/{key}", response_model=SuccessResponse[dict], dependencies=_STEP_UP
)
async def update_flag(
    key: str, body: FlagPatch, admin_id: Admin, db: Db
) -> SuccessResponse[dict]:
    """Master switch, rollout percentage and description."""
    flag = await _flag_or_404(db, key)
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    if changes.get("feature_key"):
        await _feature_or_404(db, changes["feature_key"])
    old = {field: getattr(flag, field) for field in changes}
    for field, value in changes.items():
        setattr(flag, field, value)
    await AuditService(db).log(
        event_type="flag.update",
        action="update",
        resource_type="flag",
        resource_id=key,
        user_id=admin_id,
        severity="warning" if changes.get("enabled") is False else "info",
        old_value=old,
        new_value=changes,
        details={"reason": body.reason},
    )
    await db.commit()
    await EntitlementService.bump_catalog()
    targets = await db.scalar(
        select(func.count())
        .select_from(FeatureFlagTargetModel)
        .where(FeatureFlagTargetModel.flag_key == key)
    )
    return SuccessResponse(data=_flag_out(flag, targets, datetime.now(UTC)))


@router.delete("/flags/{key}", status_code=204, dependencies=_STEP_UP)
async def delete_flag(
    key: str,
    admin_id: Admin,
    db: Db,
    reason: Annotated[str, Query(min_length=3, max_length=500)],
) -> None:
    """Retire a flag once its code path is gone. Its targets go with it."""
    flag = await _flag_or_404(db, key)
    old = {"enabled": flag.enabled, "rollout_percent": flag.rollout_percent}
    await db.delete(flag)
    await AuditService(db).log(
        event_type="flag.delete",
        action="delete",
        resource_type="flag",
        resource_id=key,
        user_id=admin_id,
        old_value=old,
        details={"reason": reason},
    )
    await db.commit()
    await EntitlementService.bump_catalog()


@router.get("/flags/{key}/targets", response_model=SuccessResponse[list])
async def list_flag_targets(key: str, _admin: Admin, db: Db) -> SuccessResponse[list]:
    await _flag_or_404(db, key)
    rows = (
        await db.execute(
            select(FeatureFlagTargetModel, TenantModel.name, TenantModel.subdomain)
            .join(TenantModel, TenantModel.id == FeatureFlagTargetModel.tenant_id)
            .where(FeatureFlagTargetModel.flag_key == key)
            .order_by(FeatureFlagTargetModel.created_at.desc())
        )
    ).all()
    now = datetime.now(UTC)
    return SuccessResponse(
        data=[
            {
                "tenant_id": str(t.tenant_id),
                "tenant": {"name": name, "subdomain": subdomain},
                "enabled": t.enabled,
                "expires_at": _iso(t.expires_at),
                "expired": t.expires_at is not None and aware(t.expires_at) <= now,
                "reason": t.reason,
                "created_at": _iso(t.created_at),
            }
            for t, name, subdomain in rows
        ]
    )


class TargetIn(BaseModel):
    enabled: bool = True
    expires_at: datetime | None = None
    reason: Reason


@router.put("/flags/{key}/targets/{tenant_id}", response_model=SuccessResponse[dict])
async def set_flag_target(
    key: str, tenant_id: UUID, body: TargetIn, admin_id: Admin, db: Db
) -> SuccessResponse[dict]:
    """Put one merchant in (or hold one out of) a rollout."""
    await _flag_or_404(db, key)
    await _tenant_or_404(db, tenant_id)
    insert = pg_insert(FeatureFlagTargetModel).values(
        flag_key=key,
        tenant_id=tenant_id,
        enabled=body.enabled,
        expires_at=body.expires_at,
        reason=body.reason.strip(),
        created_by=admin_id,
    )
    await db.execute(
        insert.on_conflict_do_update(
            index_elements=["flag_key", "tenant_id"],
            set_={
                "enabled": body.enabled,
                "expires_at": body.expires_at,
                "reason": body.reason.strip(),
                "created_by": admin_id,
            },
        )
    )
    await TenantRepository(db).bump_entitlements_version(tenant_id)
    await AuditService(db).log(
        event_type="flag.target.set",
        action="set",
        resource_type="flag",
        resource_id=key,
        tenant_id=tenant_id,
        user_id=admin_id,
        new_value={"enabled": body.enabled, "expires_at": _iso(body.expires_at)},
        details={"reason": body.reason},
    )
    return SuccessResponse(
        data={
            "flag_key": key,
            "tenant_id": str(tenant_id),
            "enabled": body.enabled,
            "expires_at": _iso(body.expires_at),
        }
    )


@router.delete("/flags/{key}/targets/{tenant_id}", status_code=204)
async def remove_flag_target(
    key: str,
    tenant_id: UUID,
    admin_id: Admin,
    db: Db,
    reason: Annotated[str, Query(min_length=3, max_length=500)],
) -> None:
    result = await db.execute(
        delete(FeatureFlagTargetModel).where(
            FeatureFlagTargetModel.flag_key == key,
            FeatureFlagTargetModel.tenant_id == tenant_id,
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="No such target")
    await TenantRepository(db).bump_entitlements_version(tenant_id)
    await AuditService(db).log(
        event_type="flag.target.remove",
        action="remove",
        resource_type="flag",
        resource_id=key,
        tenant_id=tenant_id,
        user_id=admin_id,
        details={"reason": reason},
    )


@router.get("/flags/{key}/evaluate", response_model=SuccessResponse[dict])
async def evaluate_flag(
    key: str, tenant_id: UUID, _admin: Admin, db: Db
) -> SuccessResponse[dict]:
    """Is this flag on for this merchant, and why."""
    flag = await _flag_or_404(db, key)
    await _tenant_or_404(db, tenant_id)
    target = await db.get(FeatureFlagTargetModel, (key, tenant_id))
    on, why = flag_on(
        Flag(flag.key, flag.enabled, flag.rollout_percent),
        str(tenant_id),
        FlagTarget(target.enabled, aware(target.expires_at)) if target else None,
        datetime.now(UTC),
    )
    return SuccessResponse(
        data={"on": on, "why": why, "bucket": bucket(key, str(tenant_id))}
    )


# ─── History ──────────────────────────────────────────────────────────────


@router.get("/audit", response_model=SuccessResponse[list])
async def entitlement_audit(
    _admin: Admin,
    db: Db,
    feature: str | None = None,
    flag: str | None = None,
    tenant_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SuccessResponse[list]:
    """Who changed what, when, from what, and why."""
    stmt = (
        select(AuditLogModel, UserModel.email)
        .outerjoin(UserModel, UserModel.id == AuditLogModel.user_id)
        .where(
            or_(
                AuditLogModel.event_type.like("entitlement.%"),
                AuditLogModel.event_type.like("flag.%"),
            )
        )
    )
    if feature:
        stmt = stmt.where(
            AuditLogModel.resource_type == "feature",
            AuditLogModel.resource_id == feature,
        )
    if flag:
        stmt = stmt.where(
            AuditLogModel.resource_type == "flag", AuditLogModel.resource_id == flag
        )
    if tenant_id:
        stmt = stmt.where(AuditLogModel.tenant_id == tenant_id)
    rows = (
        await db.execute(stmt.order_by(AuditLogModel.created_at.desc()).limit(limit))
    ).all()
    return SuccessResponse(
        data=[
            {
                "id": str(entry.id),
                "event_type": entry.event_type,
                "action": entry.action,
                "severity": entry.severity,
                "resource_type": entry.resource_type,
                "resource_id": entry.resource_id,
                "tenant_id": entry.tenant_id and str(entry.tenant_id),
                "actor": email,
                "details": entry.details,
                "created_at": _iso(entry.created_at),
            }
            for entry, email in rows
        ]
    )
