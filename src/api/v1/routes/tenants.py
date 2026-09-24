"""Tenant management routes.

Public routes for tenant/store registration and admin routes for management.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.api.dependencies.auth import (
    get_current_user_id,
    require_admin,
)
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.tenant import (
    CreateTenantRequest,
    TenantCreatedResponse,
    TenantResponse,
    UpdateTenantRequest,
)
from src.application.services.api_access import FEATURE as API_FEATURE
from src.application.services.api_access import decide as decide_api_access
from src.application.services.audit_service import AuditService
from src.application.services.entitlement_service import plan_grants
from src.config import settings
from src.infrastructure.database.models.public.entitlements import (
    EntitlementOverrideModel,
)
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
    response_model=SuccessResponse[dict],
)
async def patch_tenant_api_access(
    tenant_id: UUID,
    body: ApiAccessPatch,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[dict]:
    """Switch the public API on for a merchant whose plan does not include it.

    A thin, named wrapper over an ``api_access`` entitlement override: the
    plan matrix is too blunt for a partner on a pilot or an agency integrating
    one Starter merchant. A grant is open-ended, so it is a ``contract``
    override; revoking ends it. Either takes effect on the next request —
    existing tokens are checked on every request, and webhook deliveries stop
    with them.

    A tenant whose PLAN includes the API keeps it regardless of this grant.
    """
    await db.execute(text("SET search_path TO public"))

    tenant_repo = TenantRepository(db)
    tenant = await tenant_repo.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    now = datetime.now(UTC)
    live = await db.scalar(
        select(EntitlementOverrideModel)
        .where(
            EntitlementOverrideModel.tenant_id == tenant_id,
            EntitlementOverrideModel.feature_key == API_FEATURE,
            EntitlementOverrideModel.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if live is not None:
        live.revoked_at, live.revoked_by = now, admin_id
    if body.enabled:
        db.add(
            EntitlementOverrideModel(
                tenant_id=tenant_id,
                feature_key=API_FEATURE,
                value=True,
                starts_at=now,
                source="contract",
                reason=(body.note or "").strip() or "Public API granted by NUMU",
                created_by=admin_id,
            )
        )
    await tenant_repo.bump_entitlements_version(tenant_id)
    await AuditService(db).log(
        event_type="entitlement.override.create"
        if body.enabled
        else "entitlement.override.revoke",
        action="create" if body.enabled else "revoke",
        resource_type="feature",
        resource_id=API_FEATURE,
        tenant_id=tenant_id,
        user_id=admin_id,
        old_value={"granted": live is not None and live.value is True},
        new_value={"granted": body.enabled},
        details={"reason": body.note},
    )
    await db.commit()
    await db.refresh(tenant)

    access = await decide_api_access(db, tenant)
    logger.info(
        "tenant api access patched",
        extra={
            "tenant_id": str(tenant_id),
            "enabled": body.enabled,
            "note": body.note,
            "effective": access.allowed,
        },
    )
    # Wrapped like every other endpoint: the admin client reads `data` off
    # the envelope, so a bare dict arrives as undefined and the page throws
    # while the grant it just made has actually gone through.
    return SuccessResponse(
        data={
            "tenant_id": str(tenant_id),
            "granted": access.granted,
            "in_plan": access.in_plan,
            "allowed": access.allowed,
            "plan": access.plan,
        },
        message=(
            "Public API enabled for this merchant"
            if access.allowed
            else "Public API disabled for this merchant"
        ),
    )


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
    admin_id: Annotated[UUID, Depends(require_admin)],
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
    if request.plan is not None and request.plan != tenant.plan:
        # Only plans the entitlement catalog knows: an unknown plan would get
        # nothing but feature defaults.
        known = {k for k in await plan_grants(db) if not k.startswith("addon:")}
        if request.plan not in known:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown plan '{request.plan}'. Known: {sorted(known)}",
            )
        await AuditService(db).log(
            event_type="admin.tenant.plan_change",
            action="update",
            resource_type="tenant",
            resource_id=str(tenant_id),
            tenant_id=tenant_id,
            user_id=admin_id,
            old_value={"plan": tenant.plan},
            new_value={"plan": request.plan},
        )
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
