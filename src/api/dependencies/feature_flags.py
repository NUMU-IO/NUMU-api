"""Per-tenant feature-flag dependency factory.

Reads `tenants.feature_flags` (JSONB; map of name -> bool) and lets a
route refuse to serve when its flag is off. Per the offers-v2 rollout
plan (step 14 §2), an OFF flag returns **404** rather than 403 so the
storefront / merchant hub can't tell the feature exists yet. This
matches the pattern used elsewhere when we don't want to leak feature
existence during phased rollout.

Usage on a route::

    @router.get(
        "/promotions",
        dependencies=[Depends(require_feature_flag("ff_promotions_v2"))],
    )
    async def list_promotions(...): ...

The flag check is cheap — single SELECT on the tenants row, which is
already heavily cached (R2 / Redis layer further up the request path).

Note: this module deliberately does NOT short-circuit on superusers /
admins. The point of phased rollout is to test in production *with*
real merchants, not to bypass the gate for ourselves.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel


async def _read_feature_flags(
    session: AsyncSession,
    *,
    tenant_id: UUID | None = None,
    store_id: UUID | None = None,
) -> dict[str, bool]:
    """Resolve feature flags for the request's tenant.

    Accepts either `tenant_id` directly or a `store_id` (used by the
    storefront where the request URL carries the store, not the tenant).
    """
    if tenant_id is None and store_id is None:
        return {}
    if tenant_id is not None:
        row = (
            await session.execute(
                select(TenantModel.feature_flags).where(TenantModel.id == tenant_id)
            )
        ).scalar_one_or_none()
        return row or {}
    stmt = (
        select(TenantModel.feature_flags)
        .join(StoreModel, StoreModel.tenant_id == TenantModel.id)
        .where(StoreModel.id == store_id)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row or {}


def is_flag_enabled(flags: dict, name: str) -> bool:
    """Plain boolean flip on the loaded flags map."""
    return bool(flags.get(name, False))


def require_feature_flag(flag_name: str):
    """FastAPI dependency factory: 404 if the per-tenant flag is off.

    Resolves the tenant via the route's `store_id` Path parameter (the
    standard convention for every store-scoped route in this codebase).
    For routes that don't carry a `store_id`, define a custom checker
    inline using `_read_feature_flags(tenant_id=...)`.
    """

    async def _checker(
        store_id: Annotated[UUID, Path()],
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        flags = await _read_feature_flags(session, store_id=store_id)
        if not is_flag_enabled(flags, flag_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not Found",
            )

    return _checker
