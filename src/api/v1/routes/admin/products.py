"""Admin product management endpoints.

URL: /api/v1/admin/products
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
from src.core.entities.product import ProductStatus
from src.infrastructure.database.models.tenant.product import ProductModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AdminProductListItem(BaseModel):
    id: str
    store_id: str
    store_name: str | None = None
    tenant_id: str | None = None
    name: str
    slug: str
    sku: str | None = None
    description: str | None = None
    short_description: str | None = None
    product_type: str
    status: str
    price_amount: int
    price_currency: str
    compare_at_price: int | None = None
    cost_price: int | None = None
    quantity: int
    low_stock_threshold: int
    images: list[str] | None = None
    category_id: str | None = None
    tags: list[str] | None = None
    attributes: dict | None = None
    extra_data: dict | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(dt) -> str | None:
    return dt.isoformat() if dt else None


def _product_to_response(p: ProductModel) -> AdminProductListItem:
    store = p.store
    return AdminProductListItem(
        id=str(p.id),
        store_id=str(p.store_id),
        store_name=store.name if store else None,
        tenant_id=str(p.tenant_id) if p.tenant_id else None,
        name=p.name,
        slug=p.slug,
        sku=p.sku,
        description=p.description,
        short_description=p.short_description,
        product_type=p.product_type.value
        if hasattr(p.product_type, "value")
        else str(p.product_type),
        status=p.status.value if hasattr(p.status, "value") else str(p.status),
        price_amount=p.price_amount,
        price_currency=p.price_currency,
        compare_at_price=p.compare_at_price,
        cost_price=p.cost_price,
        quantity=p.quantity,
        low_stock_threshold=p.low_stock_threshold,
        images=p.images,
        category_id=str(p.category_id) if p.category_id else None,
        tags=p.tags,
        attributes=p.attributes,
        extra_data=p.extra_data,
        created_at=_ts(p.created_at) or "",
        updated_at=_ts(p.updated_at) or "",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[AdminProductListItem]],
    summary="List all products (admin)",
    operation_id="admin_list_products",
)
async def list_products(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    store_id: Annotated[str | None, Query()] = None,
    product_status: Annotated[str | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query()] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List all products across all stores (paginated)."""
    query = select(ProductModel).options(selectinload(ProductModel.store))
    count_query = select(func.count(ProductModel.id))

    if store_id:
        query = query.where(ProductModel.store_id == store_id)
        count_query = count_query.where(ProductModel.store_id == store_id)

    if product_status:
        try:
            parsed = ProductStatus(product_status)
            query = query.where(ProductModel.status == parsed)
            count_query = count_query.where(ProductModel.status == parsed)
        except ValueError:
            pass

    if search:
        term = f"%{search}%"
        search_filter = or_(
            ProductModel.name.ilike(term),
            ProductModel.sku.ilike(term),
            ProductModel.slug.ilike(term),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    skip = (page - 1) * limit
    query = query.order_by(ProductModel.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    products = result.scalars().unique().all()

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    items = [_product_to_response(p) for p in products]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=items,
            total=total,
            page=page,
            page_size=limit,
            total_pages=(total + limit - 1) // limit if limit > 0 else 0,
        ),
        message="Products retrieved successfully",
    )
