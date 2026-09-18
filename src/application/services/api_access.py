"""Who is allowed to use the public API, and why.

API access is sold, so it has to be switchable per merchant. Two ways in:

* the tenant's **plan** includes it (``PlanFeatures.api_access_enabled``), or
* an admin **granted** it to that specific merchant, which is the
  ``api_access`` tenant feature flag.

The grant exists because the plan matrix is a blunt instrument: an agency
integrating one Starter merchant, a partner on a pilot, or a customer who
negotiated it should not require a plan change. It reuses the feature-flag
rail rather than adding a second one — same table, same admin endpoint, same
audit trail.

``api_access_enabled`` was declared on every plan and read by nothing, so
every merchant on every plan already had the whole API. This module is what
makes the flag mean something.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.plan import get_plan_features
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel

#: The tenant feature flag an admin flips to grant access outside the plan.
GRANT_FLAG = "api_access"


@dataclass(frozen=True)
class ApiAccess:
    """Whether this merchant may use the API, and where that comes from."""

    allowed: bool
    #: "plan" | "grant" | None
    source: str | None
    plan: str
    #: True when the plan alone would allow it — the hub shows this as
    #: "included in your plan" rather than "granted by NUMU".
    in_plan: bool
    granted: bool

    @property
    def reason(self) -> str | None:
        """Why access is off, for a message a merchant can act on."""
        if self.allowed:
            return None
        return "plan_excludes_api"


async def api_access_for_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    plan: str | None = None,
    feature_flags: dict | None = None,
) -> ApiAccess:
    """Resolve API access for a tenant.

    ``plan`` and ``feature_flags`` short-circuit the query for callers that
    already hold the tenant row — the token auth path does, and it runs on
    every API request.
    """
    if plan is None or feature_flags is None:
        row = (
            await session.execute(
                select(TenantModel.plan, TenantModel.feature_flags).where(
                    TenantModel.id == tenant_id
                )
            )
        ).one_or_none()
        if row is None:
            return ApiAccess(
                allowed=False, source=None, plan="none", in_plan=False, granted=False
            )
        plan, feature_flags = row[0], row[1]

    return _decide(plan, feature_flags)


async def api_access_for_store(session: AsyncSession, store_id: UUID) -> ApiAccess:
    """Resolve API access from a store id (one join, no tenant lookup first)."""
    row = (
        await session.execute(
            select(TenantModel.plan, TenantModel.feature_flags)
            .join(StoreModel, StoreModel.tenant_id == TenantModel.id)
            .where(StoreModel.id == store_id)
        )
    ).one_or_none()
    if row is None:
        return ApiAccess(
            allowed=False, source=None, plan="none", in_plan=False, granted=False
        )
    return _decide(row[0], row[1])


def _decide(plan: str | None, feature_flags: dict | None) -> ApiAccess:
    plan_name = (plan or "free").lower()
    in_plan = bool(get_plan_features(plan_name).api_access_enabled)
    granted = bool((feature_flags or {}).get(GRANT_FLAG, False))
    return ApiAccess(
        allowed=in_plan or granted,
        source="plan" if in_plan else ("grant" if granted else None),
        plan=plan_name,
        in_plan=in_plan,
        granted=granted,
    )
