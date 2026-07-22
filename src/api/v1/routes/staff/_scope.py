"""Tenant-scope guards for staff routes.

The staff sub-routes operate on a target `membership_id` (overrides, policies,
sessions, roles) or their own id (access requests, invitations, sessions) that
arrives in the path/query/body. `require_staff_edit`/`require_staff_view`
authenticate the CALLER's membership but never check that the TARGET belongs to
the same tenant — so a staff editor of tenant A could read or modify tenant B's
memberships, overrides, policies, sessions, access requests and invitations by
id (CL-1 cross-owner, verified 2026-07-22).

These helpers make the target obey the caller's tenant. A foreign target is
reported not-found, never forbidden, so ids can't be enumerated.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)


def _caller_tenant_id(membership: object) -> str:
    tid = getattr(membership, "tenant_id", None)
    if tid is None:
        # A caller with no resolvable tenant can address nothing.
        raise HTTPException(status_code=404, detail="Not found")
    return str(tid)


async def assert_membership_in_tenant(
    db: AsyncSession, membership_id: UUID, caller: object
) -> None:
    """Refuse a target membership that isn't in the caller's tenant."""
    row = (
        await db.execute(
            select(TenantMembershipModel.tenant_id).where(
                TenantMembershipModel.id == membership_id
            )
        )
    ).scalar_one_or_none()
    if row is None or str(row) != _caller_tenant_id(caller):
        raise HTTPException(status_code=404, detail="Staff member not found")


async def assert_row_in_tenant(
    db: AsyncSession, model, row_id: UUID, caller: object, *, not_found: str
) -> None:
    """Refuse a target row (with a tenant_id column) outside the caller's tenant."""
    row = (
        await db.execute(select(model.tenant_id).where(model.id == row_id))
    ).scalar_one_or_none()
    if row is None or str(row) != _caller_tenant_id(caller):
        raise HTTPException(status_code=404, detail=not_found)
