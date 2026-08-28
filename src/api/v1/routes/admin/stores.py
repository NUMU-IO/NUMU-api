"""Admin store management endpoints.

URL: /api/v1/admin/stores
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_store_repository,
    get_user_repository,
)
from src.api.dependencies.services import (
    get_storefront_cache_service,
    get_token_service,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.config import settings
from src.core.entities.store import StoreStatus
from src.infrastructure.cache.storefront_cache import StorefrontCache
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.external_services.token_service import TokenService
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.repositories.user_repository import UserRepository
from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)

from src.infrastructure.external_services.manual_transfer import (
    MANUAL_TRANSFER_METHODS,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response / request schemas
# ---------------------------------------------------------------------------


class AdminStoreListItem(BaseModel):
    id: str
    tenant_id: str | None = None
    name: str
    slug: str
    subdomain: str | None = None
    custom_domain: str | None = None
    status: str
    owner_id: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    plan: str | None = None
    lifecycle_state: str | None = None
    is_internal: bool = False
    # Founder-merchant cohort year ("2025"), or null. Lives on the tenant.
    founder_cohort: str | None = None
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
        tenant_id=str(store.tenant_id) if store.tenant_id else None,
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
        lifecycle_state=tenant.lifecycle_state if tenant else None,
        is_internal=tenant.is_internal if tenant else False,
        founder_cohort=tenant.founder_cohort if tenant else None,
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


@router.get(
    "/{store_id}/detail",
    response_model=SuccessResponse[dict],
    summary="Full merchant profile for one store (admin)",
    operation_id="admin_get_store_detail",
)
async def get_store_detail(
    store_id: Annotated[UUID, Path(description="Store ID")],
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Everything the admin needs about one merchant on a single page:
    store, tenant lifecycle/billing (incl. demo lead capture for demo
    tenants), owner account, wallet summary (payg), commerce metrics,
    and the most recent orders. Works for real merchants AND demos."""
    from src.core.entities.order import PaymentStatus
    from src.infrastructure.database.models.public.wallet import (
        MerchantWalletModel,
    )
    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.database.models.tenant.product import ProductModel

    store = (
        await db.execute(select(StoreModel).where(StoreModel.id == store_id))
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    tenant = None
    if store.tenant_id:
        tenant = (
            await db.execute(
                select(TenantModel).where(TenantModel.id == store.tenant_id)
            )
        ).scalar_one_or_none()

    owner = None
    if store.owner_id:
        owner = (
            await db.execute(select(UserModel).where(UserModel.id == store.owner_id))
        ).scalar_one_or_none()

    wallet = None
    if tenant is not None:
        wallet = (
            await db.execute(
                select(MerchantWalletModel).where(
                    MerchantWalletModel.tenant_id == tenant.id
                )
            )
        ).scalar_one_or_none()

    # Commerce metrics — one aggregate per table, store-scoped.
    orders_row = (
        await db.execute(
            select(
                func.count(OrderModel.id),
                func.coalesce(
                    func.sum(OrderModel.total).filter(
                        OrderModel.payment_status == PaymentStatus.PAID
                    ),
                    0,
                ),
                func.max(OrderModel.created_at),
            ).where(OrderModel.store_id == store_id)
        )
    ).one()
    products_count = (
        await db.execute(
            select(func.count(ProductModel.id)).where(ProductModel.store_id == store_id)
        )
    ).scalar_one()
    customers_count = (
        await db.execute(
            select(func.count(CustomerModel.id)).where(
                CustomerModel.store_id == store_id
            )
        )
    ).scalar_one()

    recent_orders = (
        (
            await db.execute(
                select(OrderModel)
                .where(OrderModel.store_id == store_id)
                .order_by(OrderModel.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )

    def _iso(dt) -> str | None:
        return dt.isoformat() if dt else None

    def _enum(v) -> str | None:
        if v is None:
            return None
        return v.value if hasattr(v, "value") else str(v)

    data = {
        "store": {
            "id": str(store.id),
            "name": store.name,
            "slug": store.slug,
            "subdomain": store.subdomain,
            "custom_domain": store.custom_domain,
            "status": _enum(store.status),
            "logo_url": store.logo_url,
            "country": getattr(store, "country", None),
            "default_currency": _enum(store.default_currency),
            "default_language": store.default_language,
            "storefront_url": (
                f"https://{store.subdomain}.numueg.app" if store.subdomain else None
            ),
            "created_at": _iso(store.created_at),
        },
        "tenant": None
        if tenant is None
        else {
            "id": str(tenant.id),
            "name": tenant.name,
            "plan": tenant.plan,
            "lifecycle_state": _enum(tenant.lifecycle_state),
            "expires_at": _iso(tenant.expires_at),
            "trial_started_at": _iso(tenant.trial_started_at),
            "trial_converted_at": _iso(tenant.trial_converted_at),
            "billing_cycle": tenant.billing_cycle,
            "next_renewal_at": _iso(tenant.next_renewal_at),
            "payment_method_last4": tenant.payment_method_last4,
            "feature_flags": tenant.feature_flags or {},
            # Demo lead capture — who this demo belongs to.
            "is_demo": tenant.demo_email is not None
            or _enum(tenant.lifecycle_state) == "demo",
            "demo_name": tenant.demo_name,
            "demo_email": tenant.demo_email,
            "demo_whatsapp": tenant.demo_whatsapp,
            "demo_started_at": _iso(tenant.demo_started_at),
        },
        "owner": None
        if owner is None
        else {
            "id": str(owner.id),
            "name": f"{owner.first_name} {owner.last_name}".strip(),
            "email": str(owner.email),
            "phone": owner.phone,
            "status": _enum(owner.status),
            "plan_intent": owner.plan_intent,
            "trial_ends_at": _iso(owner.trial_ends_at),
            "last_login_at": _iso(owner.last_login_at),
            "created_at": _iso(owner.created_at),
        },
        "wallet": None
        if wallet is None
        else {
            "balance_cents": wallet.balance_cents,
            "pending_balance_cents": wallet.pending_balance_cents,
            "currency": wallet.currency,
            "status": wallet.status,
            "commission_bps_override": wallet.commission_bps_override,
        },
        "metrics": {
            "orders_count": int(orders_row[0] or 0),
            "paid_revenue_cents": int(orders_row[1] or 0),
            "last_order_at": _iso(orders_row[2]),
            "products_count": int(products_count or 0),
            "customers_count": int(customers_count or 0),
        },
        "recent_orders": [
            {
                "id": str(o.id),
                "order_number": o.order_number,
                "total_cents": o.total,
                "currency": o.currency,
                "status": _enum(o.status),
                "payment_status": _enum(o.payment_status),
                "created_at": _iso(o.created_at),
            }
            for o in recent_orders
        ],
    }
    return SuccessResponse(data=data, message="Store detail")


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
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
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

    # Evict the API's Redis cache so the resolution endpoint stops serving the
    # stale ACTIVE payload immediately.
    await cache.invalidate_store(
        store_id=store.id,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
    )

    # Bust the Next.js storefront's ISR cache too — otherwise the rendered
    # store page keeps serving from the storefront's own cache (tag
    # ``store-<subdomain>``) until its ~60s ISR window elapses, so a suspended
    # store stays visibly open. Best-effort; never fail the admin action.
    if store.subdomain:
        try:
            from src.infrastructure.external_services.nextjs_revalidation import (
                revalidate_store,
                store_cache_tags,
            )

            await revalidate_store(
                store.subdomain,
                paths=["/"],
                tags=store_cache_tags(store.subdomain, store.custom_domain),
                scope="layout",
            )
        except Exception:  # noqa: BLE001 — storefront revalidation is best-effort
            logger.warning(
                "storefront revalidation on status change failed (store=%s)",
                store.id,
                exc_info=True,
            )

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
    """Get store counts grouped by status, excluding demo and internal tenants."""
    excluded_tenant_ids = (
        select(TenantModel.id)
        .where(
            (TenantModel.lifecycle_state == TenantLifecycleState.DEMO.value)
            | (TenantModel.is_internal.is_(True))
        )
        .scalar_subquery()
    )
    result = await db.execute(
        select(StoreModel.status, func.count(StoreModel.id))
        .where(StoreModel.tenant_id.notin_(excluded_tenant_ids))
        .group_by(StoreModel.status)
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
# Toggle internal flag
# ---------------------------------------------------------------------------


class ToggleInternalRequest(BaseModel):
    is_internal: bool


class SetFounderCohortRequest(BaseModel):
    """Grant or revoke founder-merchant status.

    `founder_cohort` is the merchant's JOIN YEAR, never a rank — a rank
    would tell merchant #42 that 41 came before them, publishing the
    platform's size to every merchant and every shopper who sees the badge.
    `None` revokes.
    """

    founder_cohort: str | None = Field(
        None,
        max_length=4,
        description='Cohort year, e.g. "2025". Null revokes the badge.',
    )

    @field_validator("founder_cohort")
    @classmethod
    def _reject_a_rank(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            # "" from an empty form field means revoke, not a blank cohort.
            return None
        if not v.isdigit() or len(v) != 4:
            raise ValueError(
                "founder_cohort must be a 4-digit year, e.g. 2025. "
                "It is a cohort, not a position — a rank would leak how "
                "many merchants are on the platform."
            )
        return v


@router.patch(
    "/{store_id}/internal",
    response_model=SuccessResponse[dict],
    summary="Toggle internal flag on a store's tenant (admin)",
    operation_id="admin_toggle_internal",
)
async def toggle_internal(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: ToggleInternalRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Mark or unmark a store's tenant as internal (test/sandbox).

    Internal tenants are excluded from all admin dashboard aggregates.
    """
    store_repo = StoreRepository(db)
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found",
        )

    tenant_repo = TenantRepository(db)
    tenant = await tenant_repo.get_by_id(store.tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    tenant.is_internal = request.is_internal
    await tenant_repo.update(tenant)

    return SuccessResponse(
        data={
            "store_id": str(store.id),
            "tenant_id": str(tenant.id),
            "is_internal": tenant.is_internal,
        },
        message=f"Tenant marked as {'internal' if tenant.is_internal else 'real'}",
    )


@router.patch(
    "/{store_id}/founder",
    response_model=SuccessResponse[dict],
    summary="Grant or revoke founder-merchant status (admin)",
    operation_id="admin_set_founder_cohort",
)
async def set_founder_cohort(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: SetFounderCohortRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Mark a merchant as a founder, or clear the badge.

    Set on the TENANT, not the store: the founder is the merchant, so a
    merchant with three stores is one founder and the badge follows all
    three. Granting it from any one of their stores is therefore correct
    and intentional — the response says which tenant was changed so the
    admin UI can say so too.
    """
    store_repo = StoreRepository(db)
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    tenant_repo = TenantRepository(db)
    tenant = await tenant_repo.get_by_id(store.tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )

    previous = tenant.founder_cohort
    tenant.founder_cohort = request.founder_cohort
    await tenant_repo.update(tenant)

    logger.info(
        "admin_founder_cohort_set: tenant=%s from=%s to=%s by=%s",
        tenant.id,
        previous,
        tenant.founder_cohort,
        _admin_id,
    )

    return SuccessResponse(
        data={
            "store_id": str(store.id),
            "tenant_id": str(tenant.id),
            "founder_cohort": tenant.founder_cohort,
        },
        message=(
            f"Founder cohort set to {tenant.founder_cohort}"
            if tenant.founder_cohort
            else "Founder badge removed"
        ),
    )


# ---------------------------------------------------------------------------
# Impersonate (admin → merchant hub)
# ---------------------------------------------------------------------------


class ImpersonateResponse(BaseModel):
    dashboard_url: str
    store_id: str
    owner_id: str
    owner_email: str
    # Tokens live in the body so the admin's frontend can hand them off via
    # URL → sessionStorage. Cookies are deliberately NOT set: they would
    # land on `.numueg.app` and clobber both the admin session and any
    # parallel impersonation tab in the same browser.
    access_token: str
    refresh_token: str


@router.post(
    "/{store_id}/impersonate",
    response_model=SuccessResponse[ImpersonateResponse],
    summary="Issue merchant-hub tokens for a store's owner and return the hub URL",
    operation_id="admin_impersonate_store",
)
async def impersonate_store(
    store_id: Annotated[UUID, Path()],
    admin_id: Annotated[UUID, Depends(require_admin)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[ImpersonateResponse]:
    """Mint merchant-auth tokens so a super-admin can open the target
    store's merchant hub as its owner — **without touching any cookie on
    the admin's browser**.

    The admin frontend opens the returned ``dashboard_url`` (token in the
    URL fragment) in a new tab; the hub reads the fragment into
    ``sessionStorage`` (tab-scoped) and sends it via ``Authorization:
    Bearer`` on every request. Merchants who aren't being impersonated
    keep using cookie auth exactly as before.
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

    logger.warning(
        "admin_impersonate_store admin=%s store=%s owner=%s",
        admin_id,
        store_id,
        owner.id,
    )

    admin_user_row = await db.execute(
        select(UserModel.email).where(UserModel.id == admin_id)
    )
    admin_email = (admin_user_row.scalar_one_or_none() or "admin") or "admin"

    # Longer TTL than a normal access token: this Bearer is handed off into the
    # hub's sessionStorage and can't be refreshed there, so it must outlast a
    # work session on its own (otherwise impersonation 401s ~every 30 min).
    access = token_service.create_access_token(
        owner,
        tenant_id=store.tenant_id,
        expires_minutes=settings.impersonation_token_expire_minutes,
    )
    refresh = token_service.create_refresh_token(owner, tenant_id=store.tenant_id)

    hub_base = settings.merchant_hub_url.rstrip("/")
    # The token is in the URL *fragment*, not the query string: fragments
    # are not sent to the server and are not logged in Referer headers.
    dashboard_url = f"{hub_base}/?by={admin_email}#handoff_token={access}"

    return SuccessResponse(
        data=ImpersonateResponse(
            dashboard_url=dashboard_url,
            store_id=str(store.id),
            owner_id=str(owner.id),
            owner_email=str(owner.email),
            access_token=access,
            refresh_token=refresh,
        ),
        message="Impersonation session established",
    )


# ---------------------------------------------------------------------------
# InstaPay OCR provider routing (Phase C)
# ---------------------------------------------------------------------------
#
# Admin-only because OCR provider choice has cost (Google Vision is paid)
# and privacy (HF providers send the customer's screenshot to a public
# Space) implications. Surfacing it on the merchant hub would let any
# merchant flip themselves onto the paid tier or onto a public-data
# provider without operator awareness — neither outcome we want.


_VALID_OCR_PROVIDERS = frozenset({"none", "google_vision", "deepseek_hf", "glm_hf"})


class AdminSetOcrProviderRequest(BaseModel):
    """Set the per-store OCR provider for InstaPay proof verification.

    The frontend passes ``"none"`` to disable OCR for the store; any
    other value must match one of the impls in
    :mod:`src.infrastructure.external_services.vision`.
    """

    provider: str


class AdminOcrProviderResponse(BaseModel):
    store_id: str
    provider: str | None


@router.put(
    "/{store_id}/instapay/ocr-provider",
    response_model=SuccessResponse[AdminOcrProviderResponse],
    summary="Assign the OCR provider for a store's manual-payment proofs",
    operation_id="admin_set_instapay_ocr_provider",
)
async def admin_set_instapay_ocr_provider(
    store_id: Annotated[UUID, Path()],
    body: AdminSetOcrProviderRequest,
    admin_id: Annotated[UUID, Depends(require_admin)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> SuccessResponse[AdminOcrProviderResponse]:
    """Persist the store's OCR provider across every manual rail.

    Which OCR engine reads a store's proofs is a store-level decision,
    not a per-rail one, so this writes the same value to the InstaPay
    *and* Vodafone Cash settings blocks. Path keeps its ``instapay``
    segment for the existing backoffice call site.

    ``provider="none"`` clears the field. Anything else must match
    one of the registered impls; unknown values are rejected at the
    route boundary so a typo doesn't silently devolve to "no OCR".
    """
    provider = (body.provider or "").strip().lower()
    if provider not in _VALID_OCR_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown OCR provider '{body.provider}'. "
                f"Allowed: {sorted(_VALID_OCR_PROVIDERS)}."
            ),
        )

    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    store_settings = store.settings or {}
    payment_settings = store_settings.get("payment") or {}
    # ``"none"`` is the UX wire value for "disabled" — store as null
    # so a per-store JSONB scan doesn't have to distinguish the two.
    resolved = None if provider == "none" else provider
    for rail in MANUAL_TRANSFER_METHODS:
        rail_settings = payment_settings.get(rail) or {}
        rail_settings["ocr_provider"] = resolved
        payment_settings[rail] = rail_settings
    store_settings["payment"] = payment_settings
    store.settings = store_settings
    await store_repo.update(store)

    logger.warning(
        "admin_set_instapay_ocr_provider admin=%s store=%s provider=%s",
        admin_id,
        store_id,
        resolved,
    )

    return SuccessResponse(
        data=AdminOcrProviderResponse(
            store_id=str(store.id),
            provider=resolved,
        ),
        message="OCR provider updated",
    )


# ---------------------------------------------------------------------------
# Theme snapshots — admin support tooling (Session C 2026-05-28)
# ---------------------------------------------------------------------------
#
# Read-only listing of ``store_theme_snapshots`` for a single store.
# Used by the numu-admin `/marketplace/snapshots/{storeId}` page so
# support can inspect "what state was this store in before the merchant
# switched themes." A snapshot is written automatically by the
# ThemeActivationService whenever a destructive write fires — see
# `src/infrastructure/repositories/store_theme_snapshot_repository.py`.
#
# NO RESTORE ENDPOINT in this session — restoring touches `store_themes`
# and triggers a downstream chain. Session C UI shows the Restore button
# as disabled-with-tooltip; the restore endpoint is deferred to a
# follow-up session pending explicit user authorization.


class AdminSnapshotItem(BaseModel):
    """One row in the admin snapshot browser."""

    id: str
    store_id: str
    theme_id: str | None = None
    theme_version_id: str | None = None
    reason: str
    created_at: str
    restored_at: str | None = None
    # Sizing signals — let the UI render "4 sections customized" without
    # downloading the full customization payload. Counts are derived
    # server-side to keep the wire payload small (a fully-customized
    # store_theme_snapshot.customization_v3 can be 50-100 KB).
    section_count: int = 0
    section_group_count: int = 0
    # Optional resolved theme name for the UI's "transition" hint
    # ("Bon Younes → Empire"). NULL when the snapshot's theme_id was
    # SET NULL by a downstream theme delete (rare, but possible).
    theme_name: str | None = None


class AdminSnapshotListResponse(BaseModel):
    snapshots: list[AdminSnapshotItem]


@router.get(
    "/{store_id}/themes/snapshots",
    response_model=SuccessResponse[AdminSnapshotListResponse],
    summary="List theme snapshots for a store (admin support tooling)",
    operation_id="admin_list_store_theme_snapshots",
)
async def list_store_theme_snapshots(
    store_id: Annotated[UUID, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[dict, Depends(require_admin)],
    limit: int = Query(20, ge=1, le=100),
) -> SuccessResponse[AdminSnapshotListResponse]:
    """List snapshots for a single store, newest first.

    Read-only. The snapshot rows themselves are append-only; the only
    mutation field is ``restored_at`` which gets stamped by the (future)
    restore endpoint. Currently no restore endpoint exists; the
    numu-admin UI surfaces the Restore button as disabled-with-tooltip.

    Returns up to ``limit`` rows (default 20, max 100). Includes
    restored snapshots in the list so the admin sees the full audit
    trail.
    """
    from src.infrastructure.database.models.tenant.theme import (
        ThemeModel,
    )
    from src.infrastructure.repositories.store_theme_snapshot_repository import (
        StoreThemeSnapshotRepository,
    )

    snapshot_repo = StoreThemeSnapshotRepository(db)
    rows = await snapshot_repo.list_for_store(store_id=store_id, limit=limit)

    # Resolve theme names in a single bulk query so the UI can render
    # the from-theme name on each row without N round-trips. The
    # snapshot's theme_id points at `themes` (the runtime catalog) —
    # see `core/entities/theme.py`. A NULL theme_id means the theme
    # was deleted after the snapshot was taken (rare).
    theme_ids = {r.theme_id for r in rows if r.theme_id is not None}
    theme_name_by_id: dict[UUID, str] = {}
    if theme_ids:
        name_result = await db.execute(
            select(ThemeModel.id, ThemeModel.name).where(ThemeModel.id.in_(theme_ids))
        )
        theme_name_by_id = dict(name_result.all())

    items: list[AdminSnapshotItem] = []
    for row in rows:
        # Cheap section-count signals so the UI can show "N sections
        # customized" badges without downloading the full payload.
        cust_v3 = row.customization_v3 or {}
        templates = cust_v3.get("templates", {}) if isinstance(cust_v3, dict) else {}
        section_count = 0
        if isinstance(templates, dict):
            for tpl in templates.values():
                if isinstance(tpl, dict):
                    sections = tpl.get("sections", {})
                    if isinstance(sections, dict):
                        section_count += len(sections)
                    elif isinstance(sections, list):
                        section_count += len(sections)
        section_groups = (
            cust_v3.get("section_groups", {}) if isinstance(cust_v3, dict) else {}
        )
        section_group_count = (
            len(section_groups) if isinstance(section_groups, dict) else 0
        )

        items.append(
            AdminSnapshotItem(
                id=str(row.id),
                store_id=str(row.store_id),
                theme_id=str(row.theme_id) if row.theme_id else None,
                theme_version_id=(
                    str(row.theme_version_id) if row.theme_version_id else None
                ),
                reason=row.reason,
                created_at=row.created_at.isoformat() if row.created_at else "",
                restored_at=(row.restored_at.isoformat() if row.restored_at else None),
                section_count=section_count,
                section_group_count=section_group_count,
                theme_name=theme_name_by_id.get(row.theme_id) if row.theme_id else None,
            )
        )

    return SuccessResponse(
        data=AdminSnapshotListResponse(snapshots=items),
        message=f"{len(items)} snapshot(s) retrieved",
    )


class AdminSnapshotPayloadResponse(BaseModel):
    """Full snapshot detail — used by the [View JSON] modal."""

    id: str
    store_id: str
    reason: str
    created_at: str
    restored_at: str | None = None
    customization: dict
    customization_v3: dict


@router.get(
    "/{store_id}/themes/snapshots/{snapshot_id}",
    response_model=SuccessResponse[AdminSnapshotPayloadResponse],
    summary="Get full snapshot payload (admin support tooling)",
    operation_id="admin_get_store_theme_snapshot",
)
async def get_store_theme_snapshot(
    store_id: Annotated[UUID, Path()],
    snapshot_id: Annotated[UUID, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[dict, Depends(require_admin)],
) -> SuccessResponse[AdminSnapshotPayloadResponse]:
    """Fetch a single snapshot's full JSON payload. Lets the admin
    inspect ``customization`` + ``customization_v3`` for a forensic
    look at what state the merchant's store was in before the
    snapshot-triggering write.

    Returns 404 if the snapshot doesn't belong to the named store
    (defence in depth — the URL-level scoping prevents path-traversal
    attempts).
    """
    from src.infrastructure.database.models.tenant.theme import (
        StoreThemeSnapshotModel,
    )

    result = await db.execute(
        select(StoreThemeSnapshotModel).where(
            StoreThemeSnapshotModel.id == snapshot_id,
            StoreThemeSnapshotModel.store_id == store_id,
        )
    )
    snap = result.scalar_one_or_none()
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Snapshot not found for this store",
        )

    return SuccessResponse(
        data=AdminSnapshotPayloadResponse(
            id=str(snap.id),
            store_id=str(snap.store_id),
            reason=snap.reason,
            created_at=snap.created_at.isoformat() if snap.created_at else "",
            restored_at=(snap.restored_at.isoformat() if snap.restored_at else None),
            customization=snap.customization or {},
            customization_v3=snap.customization_v3 or {},
        ),
        message="Snapshot payload retrieved",
    )
