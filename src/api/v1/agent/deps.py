"""Dependencies for the Agent routes.

Resolves the authenticated staff member, the tenant, and the store (from the
path, verified against the tenant), plus a `has_permission` callable the tool
layer uses to fail closed. All of this reuses NUMU-api's existing auth + tenant
+ RBAC plumbing (Constitution I & IV) — the Agent adds no new auth surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.permissions import get_current_membership
from src.api.dependencies.tenant import get_current_tenant
from src.config import settings as app_settings
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.connection import set_tenant_id
from src.infrastructure.database.models import StoreModel
from src.infrastructure.database.models.public import TenantModel
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)
from src.infrastructure.services.permission_service import PermissionService


@dataclass
class AgentRequestContext:
    """Per-request identity + authorization handle for the Agent."""

    tenant_id: UUID
    store_id: UUID
    staff_id: UUID
    session: AsyncSession
    has_permission: object  # async (str) -> bool


async def get_agent_context(
    store_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
    membership: Annotated[TenantMembershipModel, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentRequestContext:
    if not app_settings.agent_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent is disabled"
        )

    # Verify the path store belongs to the caller's tenant (defense-in-depth on
    # top of RLS) so a tool can never read another tenant's store.
    result = await db.execute(
        select(StoreModel.id).where(
            StoreModel.id == store_id, StoreModel.tenant_id == tenant.id
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    # Build an effective-permission checker (owner short-circuits to all).
    cache = RedisCacheService()
    service = PermissionService(db, cache)
    effective = await service.get_effective_permissions(membership)

    async def has_permission(code: str) -> bool:
        if membership.is_owner:
            return True
        return effective.has_permission(code)

    # Seed the tenant ContextVar the agent repositories read via get_tenant_id.
    # The tenant middleware only sets it when the request carries a tenant host,
    # which a hub call to the shared API domain does not, so every agent route
    # except /chat was failing closed with "No tenant context" — the history
    # list came back empty and the digest never rendered. /chat happened to work
    # because it re-seeds this itself: a StreamingResponse body is iterated
    # after the endpoint returns, once the request-scoped value is already gone.
    # Setting it here covers every route that shares this dependency; /chat
    # still needs its own call for that later-context reason.
    set_tenant_id(tenant.id)

    return AgentRequestContext(
        tenant_id=tenant.id,
        store_id=store_id,
        staff_id=user_id,
        session=db,
        has_permission=has_permission,
    )
