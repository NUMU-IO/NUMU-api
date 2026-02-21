"""Admin store management endpoints.

URL: /api/v1/admin/stores
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.core.entities.store import StoreStatus
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response / request schemas
# ---------------------------------------------------------------------------


class AdminStoreListItem(BaseModel):
    id: str
    name: str
    slug: str
    subdomain: str | None = None
    custom_domain: str | None = None
    status: str
    owner_id: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    plan: str | None = None
    logo_url: str | None = None
    created_at: str


class UpdateStoreStatusRequest(BaseModel):
    status: str
    reason: str | None = None


class StoreStatsResponse(BaseModel):
    total: int
    active: int
    pending_approval: int
    suspended: int
    inactive: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(dt) -> str | None:
    return dt.isoformat() if dt else None


def _store_to_list_item(
    store: StoreModel,
    owner: UserModel | None = None,
    tenant: TenantModel | None = None,
) -> AdminStoreListItem:
    return AdminStoreListItem(
        id=str(store.id),
        name=store.name,
        slug=store.slug,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
        status=store.status.value
        if hasattr(store.status, "value")
        else str(store.status),
        owner_id=str(store.owner_id) if store.owner_id else None,
        owner_name=f"{owner.first_name} {owner.last_name}" if owner else None,
        owner_email=owner.email if owner else None,
        plan=tenant.plan if tenant else None,
        logo_url=store.logo_url,
        created_at=_ts(store.created_at) or "",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[AdminStoreListItem]],
    summary="List all stores (admin)",
    operation_id="admin_list_stores",
)
async def list_stores(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    store_status: Annotated[str | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query()] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List all stores across the platform (paginated)."""
    query = select(StoreModel)
    count_query = select(func.count(StoreModel.id))

    # Status filter
    if store_status:
        try:
            parsed = StoreStatus(store_status)
            query = query.where(StoreModel.status == parsed)
            count_query = count_query.where(StoreModel.status == parsed)
        except ValueError:
            pass

    # Search filter (name or subdomain)
    if search:
        term = f"%{search}%"
        search_filter = or_(
            StoreModel.name.ilike(term),
            StoreModel.subdomain.ilike(term),
            StoreModel.slug.ilike(term),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Pagination
    skip = (page - 1) * limit
    query = query.order_by(StoreModel.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    stores = list(result.scalars().all())

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Batch-fetch owners and tenants for the page
    owner_ids = {s.owner_id for s in stores if s.owner_id}
    tenant_ids = {s.tenant_id for s in stores if s.tenant_id}

    owners_map: dict[str, UserModel] = {}
    if owner_ids:
        owners_result = await db.execute(
            select(UserModel).where(UserModel.id.in_(owner_ids))
        )
        for u in owners_result.scalars().all():
            owners_map[str(u.id)] = u

    tenants_map: dict[str, TenantModel] = {}
    if tenant_ids:
        tenants_result = await db.execute(
            select(TenantModel).where(TenantModel.id.in_(tenant_ids))
        )
        for t in tenants_result.scalars().all():
            tenants_map[str(t.id)] = t

    items = [
        _store_to_list_item(
            s,
            owner=owners_map.get(str(s.owner_id)),
            tenant=tenants_map.get(str(s.tenant_id)),
        )
        for s in stores
    ]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=items,
            total=total,
            page=page,
            page_size=limit,
            total_pages=(total + limit - 1) // limit if limit > 0 else 0,
        ),
        message="Stores retrieved successfully",
    )


@router.patch(
    "/{store_id}/status",
    response_model=SuccessResponse[dict],
    summary="Update store status (admin)",
    operation_id="admin_update_store_status",
)
async def update_store_status(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: UpdateStoreStatusRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a store's status (approve, suspend, activate, deactivate)."""
    store_repo = StoreRepository(db)
    tenant_repo = TenantRepository(db)

    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found",
        )

    # Parse target status
    try:
        new_status = StoreStatus(request.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {request.status}. "
            f"Valid: {[s.value for s in StoreStatus]}",
        )

    was_pending = store.status == StoreStatus.PENDING_APPROVAL

    # Apply domain method based on target status
    if new_status == StoreStatus.ACTIVE:
        if was_pending:
            store.approve()
        else:
            store.activate()
    elif new_status == StoreStatus.SUSPENDED:
        store.suspend(request.reason)
    elif new_status == StoreStatus.INACTIVE:
        store.deactivate()
    elif new_status == StoreStatus.PENDING_APPROVAL:
        store.status = StoreStatus.PENDING_APPROVAL
        store.touch()

    await store_repo.update(store)

    # Sync tenant.is_active
    if store.tenant_id:
        tenant = await tenant_repo.get_by_id(store.tenant_id)
        if tenant:
            tenant.is_active = new_status == StoreStatus.ACTIVE
            await tenant_repo.update(tenant)

    await db.commit()

    # Dispatch approval email if store was just approved
    if was_pending and new_status == StoreStatus.ACTIVE:
        try:
            from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
                send_store_approved_email_task,
            )

            send_store_approved_email_task.delay(str(store.id))
        except Exception:
            logger.warning(
                f"Failed to dispatch approval email for store {store.id}",
                exc_info=True,
            )

    return SuccessResponse(
        data={"id": str(store.id), "status": new_status.value},
        message=f"Store status updated to {new_status.value}",
    )


@router.get(
    "/stats",
    response_model=SuccessResponse[StoreStatsResponse],
    summary="Store statistics (admin)",
    operation_id="admin_store_stats",
)
async def store_stats(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get store counts grouped by status."""
    result = await db.execute(
        select(StoreModel.status, func.count(StoreModel.id)).group_by(StoreModel.status)
    )
    counts = {row[0]: row[1] for row in result.all()}

    # Map enum members to counts — handle both enum objects and raw strings
    def _count(s: StoreStatus) -> int:
        # Try enum value first (what DB returns may vary)
        return counts.get(s, 0) or counts.get(s.value, 0) or counts.get(s.name, 0)

    active = _count(StoreStatus.ACTIVE)
    pending = _count(StoreStatus.PENDING_APPROVAL)
    suspended = _count(StoreStatus.SUSPENDED)
    inactive = _count(StoreStatus.INACTIVE)

    return SuccessResponse(
        data=StoreStatsResponse(
            total=active + pending + suspended + inactive,
            active=active,
            pending_approval=pending,
            suspended=suspended,
            inactive=inactive,
        ),
        message="Store stats retrieved successfully",
    )
