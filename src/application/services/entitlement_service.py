"""The one place that answers "may this tenant use X, and how much of it".

Callers ask ``require`` / ``has`` / ``limit`` / ``flag`` / ``check_quota`` /
``consume`` and never learn where a grant came from. Admin tools ask
``explain``.

A read costs one Redis round trip. The tenant's resolved snapshot is stamped
with ``[tenants.entitlements_version, catalog token, tenants.plan]``; a stamp
that no longer matches is recomputed on the spot. Writers bump a version
instead of deleting a key (``TenantRepository.bump_entitlements_version`` in
the writer's transaction for one tenant, ``bump_catalog`` after the commit for
everyone), so there is no delete-then-stale-refill race, and a plan change
needs no invalidation code at all: the plan is in the stamp.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.app_billing import coverage_end
from src.core.entities.plan import PLAN_LIMITS, get_plan_features
from src.core.entitlements import (
    UNLIMITED,
    Feature,
    Flag,
    FlagTarget,
    Grant,
    Kind,
    Resolved,
    bucket,
    flag_on,
    is_on,
    next_change,
    resolve,
)
from src.core.exceptions import (
    FeatureDisabledError,
    FeatureNotAvailableError,
    PlanLimitExceededError,
)
from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.public.app_billing import (
    AppSubscriptionModel,
)
from src.infrastructure.database.models.public.entitlements import (
    EntitlementOverrideModel,
    FeatureFlagModel,
    FeatureFlagTargetModel,
    FeatureModel,
    PlanEntitlementModel,
    UsageCounterModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

CATALOG_KEY = "ent:catalog"
#: The safety net for a missed bump. Invalidation is the stamp, not this.
SNAPSHOT_TTL = timedelta(minutes=5)
LIFETIME = datetime(1970, 1, 1, tzinfo=UTC)
#: Fields the merchant hub gets; source_id stays server-side.
PUBLIC_FIELDS = (
    "value",
    "available",
    "in_plan",
    "source",
    "expires_at",
    "reason",
    "kind",
    "period",
)

Counter = Callable[[AsyncSession, TenantModel, datetime | None], Awaitable[int]]


def _tenant_stores(tenant: TenantModel) -> Select[Any]:
    return select(StoreModel.id).where(StoreModel.tenant_id == tenant.id)


async def _count_products(
    db: AsyncSession, tenant: TenantModel, since: datetime | None
) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(ProductModel)
            .where(ProductModel.store_id.in_(_tenant_stores(tenant)))
        )
        or 0
    )


async def _count_orders(
    db: AsyncSession, tenant: TenantModel, since: datetime | None
) -> int:
    # Bounded by ix_orders_store_created_status: at most `limit` index entries
    # are ever walked for a tenant that has a limit at all.
    return int(
        await db.scalar(
            select(func.count())
            .select_from(OrderModel)
            .where(
                OrderModel.store_id.in_(_tenant_stores(tenant)),
                OrderModel.created_at >= since,
            )
        )
        or 0
    )


async def _count_stores(
    db: AsyncSession, tenant: TenantModel, since: datetime | None
) -> int:
    # Owner level, like store creation: every store the owner holds outside a
    # partner's development stores, which have their own cap.
    return int(
        await db.scalar(
            select(func.count(StoreModel.id))
            .join(TenantModel, TenantModel.id == StoreModel.tenant_id)
            .where(
                TenantModel.owner_id == tenant.owner_id,
                TenantModel.plan != "developer",
            )
        )
        or 0
    )


async def _count_staff(
    db: AsyncSession, tenant: TenantModel, since: datetime | None
) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(TenantMembershipModel)
            .where(
                TenantMembershipModel.tenant_id == tenant.id,
                TenantMembershipModel.is_owner.is_(False),
                TenantMembershipModel.deleted_at.is_(None),
            )
        )
        or 0
    )


async def _count_partner_apps(
    db: AsyncSession, tenant: TenantModel, since: datetime | None
) -> int:
    # NUMU Apps never count; only apps with a partner developer do.
    return int(
        await db.scalar(
            select(func.count())
            .select_from(AppInstallationModel)
            .join(AppModel, AppModel.id == AppInstallationModel.app_id)
            .where(
                AppInstallationModel.store_id.in_(_tenant_stores(tenant)),
                AppModel.developer_id.isnot(None),
            )
        )
        or 0
    )


#: usage = "count" features: the real rows are the meter. Keep this list in
#: step with the catalog; a missing entry fails loudly in check_quota.
COUNTERS: dict[str, Counter] = {
    "products": _count_products,
    "orders_per_month": _count_orders,
    "stores": _count_stores,
    "staff_accounts": _count_staff,
    "partner_apps": _count_partner_apps,
}


#: PLAN_LIMITS field → catalog key, for the legacy endpoints that still speak
#: the old field names (/stores/{id}/plan, /admin/plan-limits).
LEGACY_FIELDS = {
    "max_products": "products",
    "max_orders_per_month": "orders_per_month",
    "max_stores": "stores",
    "max_staff_members": "staff_accounts",
    "max_partner_apps": "partner_apps",
    "api_access_enabled": "api_access",
    "custom_domain_enabled": "custom_domain",
    "discount_codes_enabled": "discount_codes",
}


async def plan_grants(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """Every plan's and add-on's grants: ``{plan_key: {feature_key: value}}``."""
    grants: dict[str, dict[str, Any]] = {}
    for row in await db.scalars(select(PlanEntitlementModel)):
        grants.setdefault(row.plan_key, {})[row.feature_key] = row.value
    return grants


def period_bounds(
    period: str | None, now: datetime
) -> tuple[datetime, datetime | None]:
    """UTC calendar buckets. ``(1970-01-01, None)`` for lifetime meters."""
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, (start + timedelta(days=32)).replace(day=1)
    return LIFETIME, None


@dataclass(frozen=True)
class Inputs:
    """Everything one tenant's answers are computed from."""

    features: dict[str, FeatureModel]
    grants: dict[str, list[Grant]]
    overrides: dict[str, EntitlementOverrideModel]
    flags: list[FeatureFlagModel]
    targets: dict[str, FlagTarget]


def _feature(row: FeatureModel) -> Feature:
    return Feature(row.key, cast(Kind, row.kind), row.default_value, row.is_enabled)


def aware(moment: datetime | None) -> datetime | None:
    """UTC-aware: SQLite (the test suite) hands back naive datetimes."""
    return moment.replace(tzinfo=UTC) if moment and moment.tzinfo is None else moment


def _override(row: EntitlementOverrideModel) -> Grant:
    return Grant(
        "override", str(row.id), row.value, aware(row.starts_at), aware(row.expires_at)
    )


def _in_plan(row: FeatureModel, grants: list[Grant]) -> bool:
    """Whether the plan alone turns the feature on, whatever wins: the hub's
    "included in your plan" as opposed to "granted by NUMU"."""
    plan = next((g for g in grants if g.source == "plan"), None)
    return plan is not None and is_on(cast(Kind, row.kind), plan.value)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


class EntitlementService:
    def __init__(self, db: AsyncSession, cache: RedisCacheService | None = None):
        self.db = db
        self.cache = cache or RedisCacheService()
        self._memo: dict[UUID, dict[str, Any]] = {}

    # ─── Reads ────────────────────────────────────────────────────────────

    async def snapshot(self, tenant: TenantModel) -> dict[str, Any]:
        """The tenant's resolved features and flags. One Redis call, memoised
        for the life of this service (one request)."""
        if (snap := self._memo.get(tenant.id)) is not None:
            return snap
        key = f"ent:{tenant.id}"
        cached = await self.cache.get_many([CATALOG_KEY, key])
        # Stamp before computing: data read after the version is at least as
        # new as the version, so a stale snapshot can never carry a fresh stamp.
        stamp = [tenant.entitlements_version, cached.get(CATALOG_KEY), tenant.plan]
        now = datetime.now(UTC)
        snap = cast(dict[str, Any] | None, cached.get(key))
        if not (
            snap
            and snap["stamp"] == stamp
            and datetime.fromisoformat(snap["until"]) > now
        ):
            snap = await self._compute(tenant, stamp, now)
            ttl = datetime.fromisoformat(snap["until"]) - now
            await self.cache.set(key, snap, expire=max(1, int(ttl.total_seconds())))
        self._memo[tenant.id] = snap
        return snap

    async def feature(self, tenant: TenantModel, key: str) -> dict[str, Any]:
        state = (await self.snapshot(tenant))["features"].get(key)
        if state is None:
            # Code asked for a key the catalog lacks: fail closed, loudly.
            logger.error("entitlements_unknown_feature", feature=key)
            return {"value": False, "available": False, "reason": "unknown_feature"}
        return state

    async def has(self, tenant: TenantModel, key: str) -> bool:
        return bool((await self.feature(tenant, key))["available"])

    async def limit(self, tenant: TenantModel, key: str) -> int | str:
        """The entitled amount: an int, or UNLIMITED. The kill switch is
        enforced by require/check_quota/consume, not here."""
        value: int | str = (await self.feature(tenant, key))["value"]
        return value

    async def flag(self, tenant: TenantModel, key: str) -> bool:
        return key in (await self.snapshot(tenant))["flags"]

    async def require(self, tenant: TenantModel, key: str) -> dict[str, Any]:
        state = await self.feature(tenant, key)
        if state["available"]:
            return state
        if state["reason"] == "disabled_globally":
            raise FeatureDisabledError(key)
        via = (
            await self.available_via(key, exclude=tenant.plan)
            if state["reason"] == "not_in_plan"
            else []
        )
        raise FeatureNotAvailableError(key, reason=state["reason"], available_via=via)

    # ─── Limits ───────────────────────────────────────────────────────────

    async def check_quota(self, tenant: TenantModel, key: str, adding: int = 1) -> None:
        """Hard limit for resources counted from real rows (products, staff).
        Serialises per tenant+feature until the caller's transaction ends, so
        two concurrent creates cannot both squeeze under the limit."""
        state = await self.require(tenant, key)
        limit = state["value"]
        if limit == UNLIMITED:
            return
        dialect = getattr(getattr(self.db, "bind", None), "dialect", None)
        if dialect is not None and dialect.name == "postgresql":
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"quota:{tenant.id}:{key}"},
            )
        start, resets = period_bounds(state["period"], datetime.now(UTC))
        used = await COUNTERS[key](self.db, tenant, start)
        if used + adding > limit:
            raise await self._limit_error(tenant, key, limit, used, resets)

    async def consume(self, tenant: TenantModel, key: str, amount: int = 1) -> int:
        """Add to a metered counter (orders, messages, runs) in the caller's
        transaction. Hard limits refuse atomically; a rollback un-counts."""
        state = await self.require(tenant, key)
        limit = state["value"]
        hard = state["enforcement"] == "hard" and limit != UNLIMITED
        start, resets = period_bounds(state["period"], datetime.now(UTC))
        if hard and amount > limit:
            raise await self._limit_error(tenant, key, limit, 0, resets)
        insert = pg_insert(UsageCounterModel).values(
            tenant_id=tenant.id, feature_key=key, period_start=start, used=amount
        )
        used = await self.db.scalar(
            insert.on_conflict_do_update(
                index_elements=["tenant_id", "feature_key", "period_start"],
                set_={
                    "used": UsageCounterModel.used + insert.excluded.used,
                    "updated_at": func.now(),
                },
                where=(UsageCounterModel.used + insert.excluded.used <= limit)
                if hard
                else None,
            ).returning(UsageCounterModel.used)
        )
        if used is None:
            current = await self._counter(tenant, key, start)
            raise await self._limit_error(tenant, key, limit, current, resets)
        if limit != UNLIMITED and used > limit:
            # Soft limit crossed: V2 hands this to billing (overage) and to
            # the merchant notification centre, once per period.
            logger.insight("usage_over_soft_limit", feature=key, used=used, limit=limit)
        return used

    async def usage(self, tenant: TenantModel, key: str) -> dict[str, Any]:
        state = await self.feature(tenant, key)
        start, resets = period_bounds(state.get("period"), datetime.now(UTC))
        if state.get("usage") == "counter":
            used = await self._counter(tenant, key, start)
        else:
            used = await COUNTERS[key](self.db, tenant, start)
        limit = state["value"]
        return {
            "feature": key,
            "limit": limit,
            "used": used,
            "remaining": UNLIMITED if limit == UNLIMITED else max(0, limit - used),
            "resets_at": _iso(resets),
        }

    # ─── Explain (admin; never cached) ────────────────────────────────────

    async def compute(self, tenant: TenantModel) -> dict[str, Any]:
        """A snapshot straight from the database that never touches Redis.
        Admin screens read this: they must not wait on the cache, and a write
        they make is uncommitted until the request ends, so caching it would
        let a rolled-back change leak to merchants."""
        stamp = [tenant.entitlements_version, None, tenant.plan]
        return await self._compute(tenant, stamp, datetime.now(UTC))

    async def explain_flags(self, tenant: TenantModel) -> list[dict[str, Any]]:
        """Every release flag for this tenant: on or off, why, and its bucket."""
        now = datetime.now(UTC)
        targets = {
            t.flag_key: t
            for t in await self.db.scalars(
                select(FeatureFlagTargetModel).where(
                    FeatureFlagTargetModel.tenant_id == tenant.id
                )
            )
        }
        out = []
        for flag in await self.db.scalars(
            select(FeatureFlagModel).order_by(FeatureFlagModel.key)
        ):
            target = targets.get(flag.key)
            on, why = flag_on(
                Flag(flag.key, flag.enabled, flag.rollout_percent),
                str(tenant.id),
                FlagTarget(target.enabled, aware(target.expires_at))
                if target
                else None,
                now,
            )
            out.append({
                "key": flag.key,
                "description": flag.description,
                "enabled": flag.enabled,
                "rollout_percent": flag.rollout_percent,
                "on": on,
                "why": why,
                "bucket": bucket(flag.key, str(tenant.id)),
                "target": target
                and {
                    "enabled": target.enabled,
                    "expires_at": _iso(target.expires_at),
                    "reason": target.reason,
                },
            })
        return out

    async def explain(self, tenant: TenantModel, key: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        inputs = await self._inputs(tenant, now)
        row = inputs.features[key]
        override = inputs.overrides.get(key)
        grants = inputs.grants.get(key, [])
        result = resolve(
            _feature(row),
            bundles=grants,
            override=_override(override) if override else None,
            now=now,
        )
        cached = (await self.cache.get(f"ent:{tenant.id}") or {}).get("features", {})
        return {
            **self._public(result, row, _in_plan(row, grants)),
            "source_id": result.source_id,
            "layers": [
                {
                    "layer": "kill_switch",
                    "enabled": row.is_enabled,
                    "reason": row.disabled_reason,
                },
                {
                    "layer": "override",
                    "row": override
                    and {
                        "id": str(override.id),
                        "value": override.value,
                        "source": override.source,
                        "reason": override.reason,
                        "created_by": override.created_by and str(override.created_by),
                        "starts_at": _iso(override.starts_at),
                        "expires_at": _iso(override.expires_at),
                        "live": result.source == "override",
                    },
                },
                *(
                    {
                        "layer": g.source,
                        "id": g.source_id,
                        "value": g.value,
                        "expires_at": _iso(g.expires_at),
                        "live": g.expires_at is None or g.expires_at > now,
                        "shadowed": g in result.shadowed,
                    }
                    for g in grants
                ),
                {"layer": "default", "value": row.default_value},
            ],
            "cache_agrees": cached.get(key, {}).get("value") == result.value,
        }

    # ─── Catalog-wide invalidation ────────────────────────────────────────

    @staticmethod
    async def bump_catalog(cache: RedisCacheService | None = None) -> None:
        """AFTER the commit of a catalog, plan-grant or flag change. A random
        token, not a counter, so a Redis restart can never reissue an old one."""
        if not await (cache or RedisCacheService()).set(CATALOG_KEY, uuid4().hex):
            logger.alert("entitlements_catalog_bump_failed")

    # ─── Internals ────────────────────────────────────────────────────────

    async def _inputs(self, tenant: TenantModel, now: datetime) -> Inputs:
        bundles = {tenant.plan: Grant("plan", tenant.plan, False)}
        subs = await self.db.execute(
            select(AppModel.slug, AppSubscriptionModel)
            .join(AppModel, AppModel.id == AppSubscriptionModel.app_id)
            .where(AppSubscriptionModel.tenant_id == tenant.id)
        )
        for slug, sub in subs.all():
            # Lapsed ones too: resolve() skips them, explain() shows them.
            if (end := coverage_end(sub)) is not None:
                bundles[f"addon:{slug}"] = Grant("addon", slug, False, expires_at=end)

        grants: dict[str, list[Grant]] = {}
        rows = await self.db.scalars(
            select(PlanEntitlementModel).where(
                PlanEntitlementModel.plan_key.in_(list(bundles))
            )
        )
        seen_plan = False
        for row in rows:
            base = bundles[row.plan_key]
            seen_plan |= base.source == "plan"
            grants.setdefault(row.feature_key, []).append(
                Grant(
                    base.source, base.source_id, row.value, expires_at=base.expires_at
                )
            )
        for listed in grants.values():
            listed.sort(key=lambda g: g.source != "plan")  # plan first, then add-ons
        if not seen_plan:
            logger.alert("entitlements_unknown_plan", plan=tenant.plan)

        return Inputs(
            features={f.key: f for f in await self.db.scalars(select(FeatureModel))},
            grants=grants,
            overrides={
                o.feature_key: o
                for o in await self.db.scalars(
                    select(EntitlementOverrideModel).where(
                        EntitlementOverrideModel.tenant_id == tenant.id,
                        EntitlementOverrideModel.revoked_at.is_(None),
                    )
                )
            },
            flags=list(await self.db.scalars(select(FeatureFlagModel))),
            targets={
                t.flag_key: FlagTarget(t.enabled, aware(t.expires_at))
                for t in await self.db.scalars(
                    select(FeatureFlagTargetModel).where(
                        FeatureFlagTargetModel.tenant_id == tenant.id
                    )
                )
            },
        )

    async def _compute(
        self, tenant: TenantModel, stamp: list, now: datetime
    ) -> dict[str, Any]:
        inputs = await self._inputs(tenant, now)
        overrides = {k: _override(o) for k, o in inputs.overrides.items()}
        features = {
            key: self._public(
                resolve(
                    _feature(row),
                    bundles=inputs.grants.get(key, []),
                    override=overrides.get(key),
                    now=now,
                ),
                row,
                _in_plan(row, inputs.grants.get(key, [])),
            )
            for key, row in inputs.features.items()
        }
        flags = sorted(
            f.key
            for f in inputs.flags
            if flag_on(
                Flag(f.key, f.enabled, f.rollout_percent),
                str(tenant.id),
                inputs.targets.get(f.key),
                now,
            )[0]
        )
        boundaries = [g for gs in inputs.grants.values() for g in gs]
        boundaries += overrides.values()
        boundaries += [
            Grant("flag", k, True, expires_at=t.expires_at)
            for k, t in inputs.targets.items()
        ]
        nxt = next_change(boundaries, now)
        until = min(nxt, now + SNAPSHOT_TTL) if nxt else now + SNAPSHOT_TTL
        return {
            "stamp": stamp,
            "until": until.isoformat(),
            "features": features,
            "flags": flags,
        }

    @staticmethod
    def _public(result: Resolved, row: FeatureModel, in_plan: bool) -> dict[str, Any]:
        return {
            "value": result.value,
            "available": result.available,
            "in_plan": in_plan,
            "source": result.source,
            "source_id": result.source_id,
            "expires_at": _iso(result.expires_at),
            "reason": result.reason,
            "kind": row.kind,
            "usage": row.usage,
            "period": row.period,
            "enforcement": row.enforcement,
        }

    async def _counter(self, tenant: TenantModel, key: str, start: datetime) -> int:
        return (
            await self.db.scalar(
                select(UsageCounterModel.used).where(
                    UsageCounterModel.tenant_id == tenant.id,
                    UsageCounterModel.feature_key == key,
                    UsageCounterModel.period_start == start,
                )
            )
            or 0
        )

    async def available_via(
        self, key: str, *, beyond: Any = None, exclude: str | None = None
    ) -> list[str]:
        """Sellable plans that would turn this on, cheapest first and custom
        contracts last, then add-ons: the upsell's first choice is index 0.

        ``beyond`` keeps only grants larger than the current limit, and
        ``exclude`` drops the merchant's own plan, so a limit error never
        suggests the plan they are on or a smaller one."""
        rows = await self.db.execute(
            select(PlanEntitlementModel.plan_key, PlanEntitlementModel.value).where(
                PlanEntitlementModel.feature_key == key
            )
        )
        price = {
            k: p.monthly_price_piasters
            for k, p in PLAN_LIMITS.items()
            if p.monthly_price_piasters
        }

        def better(value: Any) -> bool:
            if value in (False, 0):
                return False
            if beyond is None:
                return True
            if beyond == UNLIMITED:
                return False
            return value == UNLIMITED or int(value) > int(beyond)

        return sorted(
            (
                plan
                for plan, value in rows.all()
                if plan != exclude
                and better(value)
                and (plan in price or plan.startswith("addon:"))
            ),
            key=lambda p: (
                p.startswith("addon:"),
                price.get(p, 0) < 0,
                price.get(p, 0),
                p,
            ),
        )

    async def _limit_error(
        self,
        tenant: TenantModel,
        key: str,
        limit: int,
        used: int,
        resets: datetime | None,
    ) -> PlanLimitExceededError:
        return PlanLimitExceededError(
            resource=key,
            limit=limit,
            current=used,
            plan=get_plan_features(tenant.plan).display_name,
            feature=key,
            resets_at=_iso(resets),
            available_via=await self.available_via(
                key, beyond=limit, exclude=tenant.plan
            ),
        )
