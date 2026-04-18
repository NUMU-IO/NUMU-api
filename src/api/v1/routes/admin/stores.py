"""Admin store management endpoints.

URL: /api/v1/admin/stores
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_store_repository,
    get_user_repository,
)
from src.api.dependencies.services import get_token_service
from src.api.responses import SuccessResponse
from src.api.utils.cookies import set_auth_cookies
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.config import settings
from src.core.entities.store import StoreStatus
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.external_services.token_service import TokenService
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.repositories.user_repository import UserRepository
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
    total_revenue: int = 0
    total_orders: int = 0
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
    total_revenue: int = 0,
    total_orders: int = 0,
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
        total_revenue=total_revenue,
        total_orders=total_orders,
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

    # Batch-aggregate revenue and order counts per store
    order_agg: dict[str, tuple[int, int]] = {}
    store_ids = [s.id for s in stores]
    if store_ids:
        agg_result = await db.execute(
            select(
                OrderModel.store_id,
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue"),
                func.count(OrderModel.id).label("order_count"),
            )
            .where(OrderModel.store_id.in_(store_ids))
            .group_by(OrderModel.store_id)
        )
        for row in agg_result.all():
            order_agg[str(row.store_id)] = (int(row.revenue), int(row.order_count))

    items = [
        _store_to_list_item(
            s,
            owner=owners_map.get(str(s.owner_id)),
            tenant=tenants_map.get(str(s.tenant_id)),
            total_revenue=order_agg.get(str(s.id), (0, 0))[0],
            total_orders=order_agg.get(str(s.id), (0, 0))[1],
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

    # Sync owner user status when store is approved
    if was_pending and new_status == StoreStatus.ACTIVE and store.owner_id:
        from src.core.entities.user import UserStatus

        owner_result = await db.execute(
            select(UserModel).where(UserModel.id == store.owner_id)
        )
        owner = owner_result.scalar_one_or_none()
        if owner and owner.status != UserStatus.ACTIVE:
            owner.status = UserStatus.ACTIVE
            await db.flush()

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


# ---------------------------------------------------------------------------
# Impersonate (admin → merchant hub)
# ---------------------------------------------------------------------------


class ImpersonateResponse(BaseModel):
    dashboard_url: str
    store_id: str
    owner_id: str
    owner_email: str


@router.post(
    "/{store_id}/impersonate",
    response_model=SuccessResponse[ImpersonateResponse],
    summary="Issue merchant-hub cookies for a store's owner and return the hub URL",
    operation_id="admin_impersonate_store",
)
async def impersonate_store(
    store_id: Annotated[UUID, Path()],
    response: Response,
    admin_id: Annotated[UUID, Depends(require_admin)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[ImpersonateResponse]:
    """Mint merchant-auth cookies so a super-admin can open the target store's
    merchant hub as its owner.

    We just issue a normal access + refresh pair for the owner user; the
    COOKIE_DOMAIN is set to the parent domain (`.numueg.app`) so the cookies
    set on the API response are visible to the merchant hub subdomain. The
    response URL carries `?impersonating=1&by=<admin_email>` so the hub can
    show a persistent banner — nothing secret lives in those params.
    """
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    owner = await user_repo.get_by_id(store.owner_id)
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store owner not found"
        )

    # Audit log — minimum bar before we wire a real audit table.
    logger.warning(
        "admin_impersonate_store admin=%s store=%s owner=%s",
        admin_id,
        store_id,
        owner.id,
    )

    # Fetch admin email for the banner; best-effort, no auth impact if missing.
    admin_user_row = await db.execute(
        select(UserModel.email).where(UserModel.id == admin_id)
    )
    admin_email = (admin_user_row.scalar_one_or_none() or "admin") or "admin"

    access = token_service.create_access_token(owner, tenant_id=store.tenant_id)
    refresh = token_service.create_refresh_token(owner, tenant_id=store.tenant_id)
    set_auth_cookies(response, access, refresh)

    hub_base = settings.merchant_hub_url.rstrip("/")
    dashboard_url = f"{hub_base}/?impersonating=1&by={admin_email}"

    return SuccessResponse(
        data=ImpersonateResponse(
            dashboard_url=dashboard_url,
            store_id=str(store.id),
            owner_id=str(owner.id),
            owner_email=str(owner.email),
        ),
        message="Impersonation session established",
    )
