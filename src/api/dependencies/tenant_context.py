"""Owner-tenant resolution for tenant-level routes (/billing, /wallet).

One user can own MULTIPLE tenants — ``CreateStoreUseCase`` mints a new
tenant per store — so the old ``scalar_one_or_none(owner_id == user)``
pattern raised ``MultipleResultsFound`` for any multi-store owner and
broke billing/wallet for them entirely.

Resolution order:

1. **Current-store context** — the merchant hub sends ``X-Tenant-Id``
   (its current store id) on every request and ``TenantMiddleware``
   resolves it onto ``request.state.tenant``. If that tenant is owned
   by the caller, it's authoritative: billing/wallet act on the store
   the merchant is actually looking at.
2. **Deterministic fallback** — the owner's most relevant tenant
   (non-demo first, newest first, LIMIT 1). Never raises on multiples.

The ownership check on path 1 matters: the header is client-supplied,
so without it a stale/foreign store id could point billing at someone
else's tenant.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_id
from src.api.dependencies.database import get_db
from src.infrastructure.database.models.public.tenant import TenantModel


async def resolve_owner_tenant(
    request: Request,
    db: AsyncSession,
    user_id: UUID,
) -> TenantModel:
    """Resolve the tenant a tenant-level route should act on. 404 if none."""
    state_tenant = getattr(request.state, "tenant", None)
    if state_tenant is not None and str(getattr(state_tenant, "owner_id", None)) == str(
        user_id
    ):
        # Re-fetch on the ROUTE's session: the middleware loaded this
        # tenant in its own short-lived session, and routes mutate the
        # returned object (subscribe, wallet config).
        tenant = (
            await db.execute(
                select(TenantModel).where(TenantModel.id == state_tenant.id)
            )
        ).scalar_one_or_none()
        if tenant is not None:
            return tenant

    tenant = (
        await db.execute(
            select(TenantModel)
            .where(TenantModel.owner_id == user_id)
            # Most relevant first: real tenants over demos, newest first.
            .order_by((TenantModel.plan == "demo").asc(), TenantModel.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="No tenant found")
    return tenant


async def get_owner_tenant(
    request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantModel:
    """FastAPI dependency form of :func:`resolve_owner_tenant`."""
    return await resolve_owner_tenant(request, db, user_id)
