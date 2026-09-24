"""FastAPI entry points to EntitlementService.

Dependencies, not middleware: most requests (every storefront page) never
ask about entitlements, so nothing is resolved until a route does.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.application.services.entitlement_service import EntitlementService
from src.core.entities.store import Store
from src.core.exceptions import FeatureNotReleasedError
from src.infrastructure.database.models.public.tenant import TenantModel


def get_entitlements(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> EntitlementService:
    """One service per request, so every check in it shares one snapshot."""
    service = getattr(request.state, "entitlements", None)
    if service is None:
        service = request.state.entitlements = EntitlementService(db)
    return service


async def store_tenant(
    request: Request,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantModel:
    """The tenant that owns the path's store. The path is authoritative (it
    was ownership-checked); the middleware's row is reused when it matches,
    which is every normal hub request, so the version check costs nothing."""
    current = getattr(request.state, "tenant", None)
    if current is not None and current.id == store.tenant_id:
        return current
    tenant = await db.get(TenantModel, store.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Store not found")
    return tenant


def require_feature(key: str):
    """403 FEATURE_NOT_AVAILABLE (or 503 when killed) unless entitled."""

    async def check(
        tenant: Annotated[TenantModel, Depends(store_tenant)],
        ents: Annotated[EntitlementService, Depends(get_entitlements)],
    ) -> None:
        await ents.require(tenant, key)

    return Depends(check)


def require_flag(key: str):
    """404 FEATURE_NOT_RELEASED unless the release flag is on for the tenant."""

    async def check(
        tenant: Annotated[TenantModel, Depends(store_tenant)],
        ents: Annotated[EntitlementService, Depends(get_entitlements)],
    ) -> None:
        if not await ents.flag(tenant, key):
            raise FeatureNotReleasedError(key)

    return Depends(check)
