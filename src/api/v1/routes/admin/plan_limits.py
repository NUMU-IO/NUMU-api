"""Admin plan limits management.

URL: /api/v1/admin/plan-limits
Requires SUPER_ADMIN role.

One familiar editor over two stores:

* prices, display names and the switches that are not entitlements yet
  (webhooks, customers, analytics) stay in ``platform_config['plan_limits']``
  and are hot-patched into the in-memory ``PLAN_LIMITS`` as before;
* the fields that ARE entitlements (products, orders, stores, staff, API
  access, custom domain, discount codes) read from and write to the
  entitlement catalog (``plan_entitlements``), which is what enforcement
  reads. The wire keeps -1 for unlimited; the catalog stores "unlimited".
"""

import logging
from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin, require_admin_2fa
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.audit_service import AuditService
from src.application.services.entitlement_service import (
    LEGACY_FIELDS,
    EntitlementService,
    plan_grants,
)
from src.core.entities.plan import PLAN_LIMITS, PlanFeatures
from src.core.entitlements import UNLIMITED, check_value
from src.infrastructure.database.models.public.entitlements import (
    PlanEntitlementModel,
)
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

logger = logging.getLogger(__name__)

router = APIRouter()

PLAN_LIMITS_KEY = "plan_limits"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanLimitsItem(BaseModel):
    key: str
    display_name: str
    # Limits
    max_products: int
    max_orders_per_month: int
    max_stores: int
    max_staff_members: int
    max_customers: int
    # Feature flags
    webhooks_enabled: bool
    custom_domain_enabled: bool
    api_access_enabled: bool
    analytics_enabled: bool
    discount_codes_enabled: bool
    # Pricing (piasters)
    monthly_price_piasters: int
    annual_price_piasters: int


class PlanLimitsResponse(BaseModel):
    plans: list[PlanLimitsItem]


class PlanLimitsUpdate(BaseModel):
    plans: list[PlanLimitsItem]


#: Fields of this editor that live in the entitlement catalog.
CATALOG_FIELDS = {
    field: feature
    for field, feature in LEGACY_FIELDS.items()
    if field in PlanLimitsItem.model_fields
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plans_to_response(grants: dict[str, dict[str, Any]]) -> list[PlanLimitsItem]:
    """PLAN_LIMITS for prices and legacy switches, the catalog for the rest."""
    items = []
    for key, pf in PLAN_LIMITS.items():
        values = asdict(pf) | {"key": key}
        for field, feature in CATALOG_FIELDS.items():
            value = grants.get(key, {}).get(feature)
            if value is not None:
                values[field] = -1 if value == UNLIMITED else value
        items.append(
            PlanLimitsItem(**{f: values[f] for f in PlanLimitsItem.model_fields})
        )
    return items


def _apply_overrides(overrides: dict[str, Any]) -> None:
    """Hot-patch PLAN_LIMITS in memory from a DB overrides dict.

    Catalog fields are skipped: a value stored here before the catalog existed
    must never shadow what enforcement reads.
    """
    for key, vals in overrides.items():
        if key not in PLAN_LIMITS or not isinstance(vals, dict):
            continue
        current = asdict(PLAN_LIMITS[key])
        current.update(
            (field, value)
            for field, value in vals.items()
            if field in current and field not in CATALOG_FIELDS
        )
        PLAN_LIMITS[key] = PlanFeatures(**current)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SuccessResponse[PlanLimitsResponse],
    summary="Get plan limits",
    operation_id="admin_get_plan_limits",
)
async def get_plan_limits(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return the current plan limits (catalog, code defaults and DB overrides)."""
    # Load overrides from DB and apply (in case they haven't been applied yet)
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == PLAN_LIMITS_KEY)
    )
    row = result.scalar_one_or_none()
    if row and isinstance(row.value, dict):
        _apply_overrides(row.value)

    return SuccessResponse(
        data=PlanLimitsResponse(plans=_plans_to_response(await plan_grants(db))),
        message="Plan limits retrieved",
    )


@router.put(
    "",
    response_model=SuccessResponse[PlanLimitsResponse],
    summary="Update plan limits",
    operation_id="admin_update_plan_limits",
    dependencies=[Depends(require_admin_2fa(max_age_seconds=300))],
)
async def update_plan_limits(
    request: PlanLimitsUpdate,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Overwrite plan limits. Prices are hot-patched into memory; catalog
    fields are written to ``plan_entitlements`` and audited, and every
    tenant's cached entitlements refresh on its next request.
    """
    grants = await plan_grants(db)
    overrides: dict[str, dict[str, Any]] = {}
    changed: list[tuple[str, str, Any, Any]] = []
    for item in request.plans:
        values = item.model_dump(exclude={"key"})
        overrides[item.key] = {
            f: v for f, v in values.items() if f not in CATALOG_FIELDS
        }
        for field, feature in CATALOG_FIELDS.items():
            kind = "boolean" if field.endswith("_enabled") else "limit"
            raw = UNLIMITED if values[field] == -1 else values[field]
            try:
                new = check_value(kind, raw)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422, detail=f"{item.key}.{field}: {exc}"
                ) from exc
            old = grants.get(item.key, {}).get(feature)
            if old != new:
                changed.append((item.key, feature, old, new))

    await db.execute(
        pg_insert(PlatformConfigModel)
        .values(
            key=PLAN_LIMITS_KEY,
            value=overrides,
            description="Plan pricing and legacy switches (admin-managed)",
        )
        .on_conflict_do_update(index_elements=["key"], set_={"value": overrides})
    )
    audit = AuditService(db)
    for plan_key, feature, old, new in changed:
        insert = pg_insert(PlanEntitlementModel).values(
            plan_key=plan_key, feature_key=feature, value=new, updated_by=admin_id
        )
        await db.execute(
            insert.on_conflict_do_update(
                index_elements=["plan_key", "feature_key"],
                set_={"value": new, "updated_by": admin_id, "updated_at": func.now()},
            )
        )
        await audit.log(
            event_type="entitlement.plan_grant.update",
            action="update",
            resource_type="feature",
            resource_id=feature,
            user_id=admin_id,
            old_value={"plan_key": plan_key, "value": old},
            new_value={"plan_key": plan_key, "value": new},
            details={"via": "plan-limits"},
        )
    await db.commit()

    _apply_overrides(overrides)
    if changed:
        await EntitlementService.bump_catalog()

    logger.info(
        "Plan limits updated by admin — plans=%s catalog_changes=%d",
        list(overrides.keys()),
        len(changed),
    )

    return SuccessResponse(
        data=PlanLimitsResponse(plans=_plans_to_response(await plan_grants(db))),
        message="Plan limits saved — changes are live immediately",
    )
