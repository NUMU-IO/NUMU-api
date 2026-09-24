"""Who is allowed to use the public API, and why.

API access is sold, so it has to be switchable per merchant. It is the
``api_access`` entitlement, so the usual ways in apply:

* the tenant's **plan** includes it (a ``plan_entitlements`` row), or
* an admin **granted** it to that specific merchant (an entitlement override;
  grants that lived in the ``api_access`` tenant feature flag were moved there
  by the entitlements migration).

The grant exists because the plan matrix is a blunt instrument: an agency
integrating one Starter merchant, a partner on a pilot, or a customer who
negotiated it should not require a plan change.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.entitlement_service import EntitlementService
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel

FEATURE = "api_access"


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


_NONE = ApiAccess(allowed=False, source=None, plan="none", in_plan=False, granted=False)


async def api_access_for_tenant(session: AsyncSession, tenant_id: UUID) -> ApiAccess:
    """Resolve API access for a tenant. The token auth path runs this on
    every API request; after the tenant row it is one cached Redis read."""
    tenant = await session.get(TenantModel, tenant_id)
    return await decide(session, tenant) if tenant is not None else _NONE


async def api_access_for_store(session: AsyncSession, store_id: UUID) -> ApiAccess:
    """Resolve API access from a store id (one join, no tenant lookup first)."""
    tenant = await session.scalar(
        select(TenantModel)
        .join(StoreModel, StoreModel.tenant_id == TenantModel.id)
        .where(StoreModel.id == store_id)
    )
    return await decide(session, tenant) if tenant is not None else _NONE


async def decide(session: AsyncSession, tenant: TenantModel) -> ApiAccess:
    state = await EntitlementService(session).feature(tenant, FEATURE)
    in_plan = bool(state.get("in_plan"))
    granted = state.get("source") == "override" and state["value"] is True
    return ApiAccess(
        allowed=bool(state["available"]),
        source="plan" if in_plan else ("grant" if granted else None),
        plan=(tenant.plan or "free").lower(),
        in_plan=in_plan,
        granted=granted,
    )
