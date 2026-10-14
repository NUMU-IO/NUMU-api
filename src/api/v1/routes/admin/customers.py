"""Admin customer management endpoints.

URL: /api/v1/admin/customers
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

import phonenumbers
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.application.services.merchant_visibility import visible
from src.application.services.network_reputation_service import (
    extract_phone_hash_from_string,
)
from src.core.entities.order import OrderStatus, PaymentStatus
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.network_reputation import (
    NetworkReputationModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel

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


class LookupCustomer(BaseModel):
    id: str
    name: str
    email: str
    store_id: str
    store_name: str


class LookupOrder(BaseModel):
    id: str
    order_number: str
    store_name: str
    status: str
    payment_status: str
    payment_method: str | None = None
    total: int
    currency: str
    created_at: str


class LookupStats(BaseModel):
    orders: int
    stores: int
    delivered: int
    returned: int
    cancelled: int
    #: Summed across currencies; stores almost never mix them per buyer.
    paid_total_cents: int
    return_rate: float | None


class LookupNetwork(BaseModel):
    total_network_orders: int
    total_network_rtos: int
    total_successful_deliveries: int
    total_refunds: int
    contributing_store_count: int
    network_risk_score: int | None
    confidence_level: str


class PhoneLookupResponse(BaseModel):
    phone: str
    customers: list[LookupCustomer]
    orders: list[LookupOrder]
    stats: LookupStats
    network: LookupNetwork | None = None


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

    query = (
        select(CustomerModel)
        .options(selectinload(CustomerModel.store))
        .where(not_excluded)
    )
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


@router.get(
    "/lookup",
    response_model=SuccessResponse[PhoneLookupResponse],
    summary="Look up one phone across every store (admin)",
    operation_id="admin_lookup_customer_phone",
)
async def lookup_phone(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    phone: Annotated[str, Query(min_length=3, max_length=40)],
):
    """Every customer row and order behind one phone, across real merchants,
    plus what the trust network knows about it."""
    raw = phone.strip()
    try:
        e164 = PhoneNumber.parse(raw, "EG").e164
        national = "0" + phonenumbers.national_significant_number(
            phonenumbers.parse(e164)
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Not a valid phone number",
        )

    customers = (
        await db.execute(
            select(CustomerModel, StoreModel.name)
            .join(StoreModel, StoreModel.id == CustomerModel.store_id)
            .where(
                # Older rows hold the phone as typed: 2010… or 010….
                CustomerModel.phone.in_({e164, raw, e164[1:], national}),
                visible(CustomerModel.tenant_id),
            )
        )
    ).all()
    theirs = OrderModel.customer_id.in_([c.id for c, _ in customers])

    orders = (
        await db.execute(
            select(OrderModel, StoreModel.name)
            .join(StoreModel, StoreModel.id == OrderModel.store_id)
            .where(theirs)
            .order_by(OrderModel.created_at.desc())
            .limit(100)
        )
    ).all()
    total, delivered, returned, cancelled, paid = (
        await db.execute(
            select(
                func.count(OrderModel.id),
                func.count().filter(OrderModel.status == OrderStatus.DELIVERED),
                func.count().filter(OrderModel.status == OrderStatus.RETURNED),
                func.count().filter(OrderModel.status == OrderStatus.CANCELLED),
                func.coalesce(
                    func.sum(OrderModel.total).filter(
                        OrderModel.payment_status == PaymentStatus.PAID
                    ),
                    0,
                ),
            ).where(theirs)
        )
    ).one()

    network = None
    phone_hash = extract_phone_hash_from_string(e164)
    if phone_hash:
        rep = await db.scalar(
            select(NetworkReputationModel).where(
                NetworkReputationModel.phone_hash == phone_hash
            )
        )
        if rep is not None:
            network = LookupNetwork.model_validate(rep, from_attributes=True)

    return SuccessResponse(
        data=PhoneLookupResponse(
            phone=e164,
            customers=[
                LookupCustomer(
                    id=str(c.id),
                    name=f"{c.first_name} {c.last_name}".strip(),
                    email=c.email,
                    store_id=str(c.store_id),
                    store_name=store_name,
                )
                for c, store_name in customers
            ],
            orders=[
                LookupOrder(
                    id=str(o.id),
                    order_number=o.order_number,
                    store_name=store_name,
                    status=o.status.value,
                    payment_status=o.payment_status.value,
                    payment_method=o.payment_method,
                    total=o.total,
                    currency=o.currency,
                    created_at=_ts(o.created_at) or "",
                )
                for o, store_name in orders
            ],
            stats=LookupStats(
                orders=total,
                stores=len({c.store_id for c, _ in customers}),
                delivered=delivered,
                returned=returned,
                cancelled=cancelled,
                paid_total_cents=int(paid),
                return_rate=returned / (delivered + returned)
                if delivered + returned
                else None,
            ),
            network=network,
        ),
        message="Phone looked up",
    )
