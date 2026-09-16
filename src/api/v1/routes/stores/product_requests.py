"""Merchant-facing product requests — the inbox for "can you get me this?".

URLs (all scoped to the merchant's own store):

* ``GET    /stores/{store_id}/product-requests``            — list, newest first
* ``PATCH  /stores/{store_id}/product-requests/{id}``       — status + note
* ``DELETE /stores/{store_id}/product-requests/{id}``       — remove one

The storefront writes these rows (see `routes/storefront/product_requests.py`);
this is where the merchant works through them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_store
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.product_request import (
    ProductRequestModel,
)

router = APIRouter()

#: What a request can be. "new" until the merchant touches it.
STATUSES = ("new", "contacted", "sourced", "closed")


class ProductRequestOut(BaseModel):
    id: str
    name: str
    email: str
    phone: str | None = None
    details: str
    images: list[str] = Field(default_factory=list)
    status: str
    note: str | None = None
    source_url: str | None = None
    locale: str | None = None
    handled_at: datetime | None = None
    created_at: datetime


class ProductRequestList(BaseModel):
    items: list[ProductRequestOut]
    total: int
    #: Requests still sitting at "new" — what the hub badges.
    new_count: int


class UpdateProductRequest(BaseModel):
    status: str | None = None
    note: str | None = Field(default=None, max_length=4000)


def _out(row: ProductRequestModel) -> ProductRequestOut:
    return ProductRequestOut(
        id=str(row.id),
        name=row.name,
        email=row.email,
        phone=row.phone,
        details=row.details,
        images=list(row.images or []),
        status=row.status,
        note=row.note,
        source_url=row.source_url,
        locale=row.locale,
        handled_at=row.handled_at,
        created_at=row.created_at,
    )


@router.get(
    "/product-requests",
    response_model=SuccessResponse[ProductRequestList],
    summary="List product requests",
    operation_id="list_product_requests",
)
async def list_product_requests(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
    request_status: Annotated[
        str | None,
        Query(alias="status", description="Filter by status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuccessResponse[ProductRequestList]:
    """Newest first, because an unanswered request ages badly."""
    base = select(ProductRequestModel).where(ProductRequestModel.store_id == store.id)
    if request_status:
        base = base.where(ProductRequestModel.status == request_status)

    rows = (
        (
            await db.execute(
                base.order_by(ProductRequestModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    # Counted over the SAME filter as the page, so a filtered list does not
    # report the unfiltered total and paginate into nothing.
    total = await db.scalar(select(func.count()).select_from(base.subquery()))

    new_count = await db.scalar(
        select(func.count())
        .select_from(ProductRequestModel)
        .where(
            ProductRequestModel.store_id == store.id,
            ProductRequestModel.status == "new",
        )
    )

    return SuccessResponse(
        data=ProductRequestList(
            items=[_out(r) for r in rows],
            total=int(total or 0),
            new_count=int(new_count or 0),
        ),
        message="Product requests retrieved",
    )


@router.patch(
    "/product-requests/{request_id}",
    response_model=SuccessResponse[ProductRequestOut],
    summary="Update a product request",
    operation_id="update_product_request",
)
async def update_product_request(
    request_id: Annotated[UUID, Path()],
    payload: UpdateProductRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[ProductRequestOut]:
    row = await db.scalar(
        select(ProductRequestModel).where(
            ProductRequestModel.id == request_id,
            ProductRequestModel.store_id == store.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Product request not found")

    if payload.status is not None:
        if payload.status not in STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of: {', '.join(STATUSES)}",
            )
        row.status = payload.status
        # Stamped the first time it leaves "new" — that is the number the
        # merchant cares about ("how long did this sit?"), so a later status
        # change must not reset it.
        if payload.status != "new" and row.handled_at is None:
            row.handled_at = datetime.now(UTC)
        elif payload.status == "new":
            row.handled_at = None

    if payload.note is not None:
        row.note = payload.note or None

    await db.commit()
    await db.refresh(row)
    return SuccessResponse(data=_out(row), message="Product request updated")


@router.delete(
    "/product-requests/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a product request",
    operation_id="delete_product_request",
)
async def delete_product_request(
    request_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(
        delete(ProductRequestModel).where(
            ProductRequestModel.id == request_id,
            ProductRequestModel.store_id == store.id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Product request not found")
