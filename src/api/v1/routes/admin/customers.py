"""Admin customer management endpoints.

URL: /api/v1/admin/customers
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AdminCustomerListItem(BaseModel):
    id: str
    store_id: str
    store_name: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    email: str
    first_name: str
    last_name: str
    phone: str | None = None
    accepts_marketing: bool
    is_verified: bool
    notes: str | None = None
    tags: list[str] | None = None
    total_orders: int
    total_spent: int
    extra_data: dict | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(dt) -> str | None:
    return dt.isoformat() if dt else None


def _customer_to_response(c: CustomerModel) -> AdminCustomerListItem:
    store = c.store
    return AdminCustomerListItem(
        id=str(c.id),
        store_id=str(c.store_id),
        store_name=store.name if store else None,
        tenant_id=str(c.tenant_id) if c.tenant_id else None,
        user_id=str(c.user_id) if c.user_id else None,
        email=c.email,
        first_name=c.first_name,
        last_name=c.last_name,
        phone=c.phone,
        accepts_marketing=c.accepts_marketing,
        is_verified=c.is_verified,
        notes=c.notes,
        tags=c.tags,
        total_orders=c.total_orders,
        total_spent=c.total_spent,
        extra_data=c.extra_data,
        created_at=_ts(c.created_at) or "",
        updated_at=_ts(c.updated_at) or "",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[AdminCustomerListItem]],
    summary="List all customers (admin)",
    operation_id="admin_list_customers",
)
async def list_customers(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    store_id: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List all customers across all stores (paginated).

    Demo and internal tenants are excluded.
    """
    excluded_tenant_ids = (
        select(TenantModel.id)
        .where(
            (TenantModel.lifecycle_state == TenantLifecycleState.DEMO.value)
            | (TenantModel.is_internal.is_(True))
        )
        .scalar_subquery()
    )
    not_excluded = CustomerModel.tenant_id.notin_(excluded_tenant_ids)

    query = select(CustomerModel).options(selectinload(CustomerModel.store)).where(not_excluded)
    count_query = select(func.count(CustomerModel.id)).where(not_excluded)

    if store_id:
        query = query.where(CustomerModel.store_id == store_id)
        count_query = count_query.where(CustomerModel.store_id == store_id)

    if search:
        term = f"%{search}%"
        search_filter = or_(
            CustomerModel.email.ilike(term),
            CustomerModel.first_name.ilike(term),
            CustomerModel.last_name.ilike(term),
            CustomerModel.phone.ilike(term),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    skip = (page - 1) * limit
    query = query.order_by(CustomerModel.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    customers = result.scalars().unique().all()

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    items = [_customer_to_response(c) for c in customers]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=items,
            total=total,
            page=page,
            page_size=limit,
            total_pages=(total + limit - 1) // limit if limit > 0 else 0,
        ),
        message="Customers retrieved successfully",
    )
