"""Tenant management routes.

Public routes for tenant/store registration and admin routes for management.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.api.dependencies.auth import (
    get_current_user_id,
    require_admin,
)
from src.api.dependencies.database import get_db
from src.api.v1.schemas.public.tenant import (
    CreateTenantRequest,
    TenantCreatedResponse,
    TenantResponse,
    UpdateTenantRequest,
)
from src.application.services.api_access import GRANT_FLAG, api_access_for_tenant
from src.config import settings
from src.infrastructure.tenancy.repository import TenantRepository
from src.infrastructure.tenancy.service import TenantService

logger = logging.getLogger(__name__)

# Public routes for tenant registration (authenticated users)
router = APIRouter()


@router.post(
    "/",
    response_model=TenantCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new store/tenant",
    description="Register a new store/tenant. Creates a new database schema for the store.",
    operation_id="create_tenant",
)
async def create_tenant(
    request: CreateTenantRequest,
    current_user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantCreatedResponse:
    """
    Create a new tenant/store.

    This endpoint:
    1. Validates the subdomain is unique and properly formatted
    2. Creates a new tenant record in the public schema
    3. Provisions a new database schema for the tenant
    4. Creates all necessary tables in the new schema

    The authenticated user becomes the owner of the new store.
    """
    tenant_service = TenantService(db)

    try:
        tenant = await tenant_service.create_tenant(
            name=request.name,
            subdomain=request.subdomain,
            owner_id=str(current_user_id),
            plan=request.plan,
        )

        # Build store URL
        base_domain = getattr(settings, "BASE_DOMAIN", "numueg.app")
        store_url = f"https://{tenant.subdomain}.{base_domain}"

        logger.info(
            f"Created new tenant: {tenant.subdomain} for user {current_user_id}"
        )

        return TenantCreatedResponse(
            message="Store created successfully",
            tenant=TenantResponse.model_validate(tenant),
            store_url=store_url,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to create tenant: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create store. Please try again later.",
        )


@router.get(
    "/check-subdomain/{subdomain}",
    summary="Check subdomain availability",
    description="Check if a subdomain is available for registration.",
    operation_id="check_subdomain_availability",
)
async def check_subdomain_availability(
    subdomain: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Check if a subdomain is available."""
    # Ensure we're querying public schema
    await db.execute(text("SET search_path TO public"))

    tenant_repo = TenantRepository(db)
    existing = await tenant_repo.get_by_subdomain(subdomain.lower())

    return {
        "subdomain": subdomain.lower(),
        "available": existing is None,
    }


# Admin routes (require super admin role)
admin_router = APIRouter()


@admin_router.get(
    "/",
    response_model=list[TenantResponse],
    summary="List all tenants",
    description="List all tenants (admin only).",
    operation_id="list_tenants",
)
async def list_tenants(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
    skip: int = 0,
    limit: int = 100,
) -> list[TenantResponse]:
    """List all tenants (admin only)."""
    await db.execute(text("SET search_path TO public"))

    tenant_repo = TenantRepository(db)
    tenants = await tenant_repo.list_all(skip=skip, limit=limit)

    return [TenantResponse.model_validate(t) for t in tenants]


@admin_router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Get tenant by ID",
    description="Get a specific tenant by ID (admin only).",
    operation_id="get_tenant",
)
async def get_tenant(
    tenant_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> TenantResponse:
    """Get tenant by ID (admin only)."""
    await db.execute(text("SET search_path TO public"))

    tenant_repo = TenantRepository(db)
    tenant = await tenant_repo.get_by_id(tenant_id)

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    return TenantResponse.model_validate(tenant)


class FeatureFlagsPatch(BaseModel):
    """A partial map of flags to flip. Absent flags are left alone."""

    flags: dict[str, bool]


@admin_router.patch(
    "/{tenant_id}/feature-flags",
    summary="Set tenant feature flags",
    description="Merge feature flags for one tenant (super admin only).",
    operation_id="patch_tenant_feature_flags",
)
async def patch_tenant_feature_flags(
    tenant_id: UUID,
    body: FeatureFlagsPatch,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> dict:
    """Flip per-tenant feature flags.

    This is the rail that did not exist. `require_feature_flag` READS this map
    to gate dark launches (404-not-403, no superuser bypass), but nothing could
    WRITE it — so every flip was hand-written SQL against the production
    database, with no audit trail.

    MERGES, never assigns. Both live tenants carry `golive_exempt: true`, and a
    plain assignment would drop it and start refusing real orders. That is the
    entire reason this endpoint exists instead of a one-line UPDATE.
    """
    await db.execute(text("SET search_path TO public"))

    tenant_repo = TenantRepository(db)
    tenant = await tenant_repo.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    before = dict(tenant.feature_flags or {})
    merged = {**before, **body.flags}
    tenant.feature_flags = merged
    # JSONB is mutable-in-place as far as SQLAlchemy is concerned; without this
    # the reassignment above can be missed and the commit writes nothing.
    flag_modified(tenant, "feature_flags")
    await db.commit()

    logger.info(
        "tenant feature flags patched",
        extra={
            "tenant_id": str(tenant_id),
            "changed": sorted(body.flags),
            "golive_exempt_preserved": before.get("golive_exempt")
            == merged.get("golive_exempt"),
        },
    )
    return {"tenant_id": str(tenant_id), "feature_flags": merged}


class ApiAccessPatch(BaseModel):
    """Grant or revoke the public API for one merchant."""

    enabled: bool
    note: str | None = None


@admin_router.patch(
    "/{tenant_id}/api-access",
    summary="Grant or revoke public API access for a tenant",
    operation_id="patch_tenant_api_access",
)
async def patch_tenant_api_access(
    tenant_id: UUID,
    body: ApiAccessPatch,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> dict:
    """Switch the public API on for a merchant whose plan does not include it.

    A thin, named wrapper over the ``api_access`` feature flag: the plan matrix
    is too blunt for a partner on a pilot or an agency integrating one Starter
    merchant, and "flip this JSON key" is not an operation anyone should have
    to remember. Revoking takes effect immediately — existing tokens are
    checked on every request, and webhook deliveries stop with them.

    A tenant whose PLAN includes the API keeps it regardless of this flag.
    """
    await db.execute(text("SET search_path TO public"))

    tenant_repo = TenantRepository(db)
    tenant = await tenant_repo.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    flags = dict(tenant.feature_flags or {})
    flags[GRANT_FLAG] = body.enabled
    tenant.feature_flags = flags
    flag_modified(tenant, "feature_flags")
    await db.commit()

    access = await api_access_for_tenant(
        db, tenant_id, plan=tenant.plan, feature_flags=flags
    )
    logger.info(
        "tenant api access patched",
        extra={
            "tenant_id": str(tenant_id),
            "enabled": body.enabled,
            "note": body.note,
            "effective": access.allowed,
        },
    )
    return {
        "tenant_id": str(tenant_id),
        "granted": access.granted,
        "in_plan": access.in_plan,
        "allowed": access.allowed,
        "plan": access.plan,
    }


@admin_router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Update tenant",
    description="Update tenant settings (admin only).",
    operation_id="update_tenant",
)
async def update_tenant(
    tenant_id: UUID,
    request: UpdateTenantRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> TenantResponse:
    """Update tenant settings (admin only)."""
    await db.execute(text("SET search_path TO public"))

    tenant_repo = TenantRepository(db)
    tenant = await tenant_repo.get_by_id(tenant_id)

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Update fields
    if request.name is not None:
        tenant.name = request.name
    if request.plan is not None:
        tenant.plan = request.plan
    if request.is_active is not None:
        tenant.is_active = request.is_active
    if request.is_internal is not None:
        tenant.is_internal = request.is_internal
    if request.settings is not None:
        tenant.settings = request.settings

    updated = await tenant_repo.update(tenant)
    await db.refresh(updated)
    return TenantResponse.model_validate(updated)


@admin_router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate tenant",
    description="Deactivate a tenant (soft delete, admin only).",
    operation_id="deactivate_tenant",
)
async def deactivate_tenant(
    tenant_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[UUID, Depends(require_admin)],
) -> None:
    """Deactivate a tenant (admin only)."""
    await db.execute(text("SET search_path TO public"))

    tenant_repo = TenantRepository(db)
    success = await tenant_repo.deactivate(tenant_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
