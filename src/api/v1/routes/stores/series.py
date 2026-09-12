"""Merchant CRUD for ordered product series."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from slugify import slugify
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.core.entities.store import Store
from src.infrastructure.cache.product_cache import get_product_cache
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.series import (
    SeriesModel,
    SeriesProductModel,
)

router = APIRouter(prefix="/{store_id}/series")


async def _invalidate_storefront(store_id: UUID) -> None:
    await get_product_cache().invalidate_store_products(store_id)


class SeriesWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255)
    description: str | None = None
    cover_image_url: str | None = Field(None, max_length=2048)
    status: str = Field("active", pattern="^(draft|active|archived)$")


class SeriesPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    cover_image_url: str | None = Field(None, max_length=2048)
    status: str | None = Field(None, pattern="^(draft|active|archived)$")


class SeriesProductWrite(BaseModel):
    product_id: UUID
    volume_label: str | None = Field(None, max_length=32)
    position: int = Field(ge=1)


class SeriesOrder(BaseModel):
    product_ids: list[UUID] = Field(min_length=1)


async def _series_or_404(
    session: AsyncSession, store_id: UUID, series_id: UUID
) -> SeriesModel:
    row = (
        await session.execute(
            select(SeriesModel).where(
                SeriesModel.id == series_id, SeriesModel.store_id == store_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Series not found")
    return row


async def _payload(session: AsyncSession, row: SeriesModel) -> dict:
    books = (
        await session.execute(
            select(SeriesProductModel, ProductModel)
            .join(ProductModel, ProductModel.id == SeriesProductModel.product_id)
            .where(SeriesProductModel.series_id == row.id)
            .order_by(SeriesProductModel.position)
        )
    ).all()
    return {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "description": row.description,
        "cover_image_url": row.cover_image_url,
        "status": row.status,
        "products": [
            {
                "product_id": str(link.product_id),
                "name": product.name,
                "slug": product.slug,
                "cover_image_url": product.images[0] if product.images else None,
                "volume_label": link.volume_label,
                "position": link.position,
            }
            for link, product in books
        ],
    }


@router.get("")
async def list_series(
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    rows = (
        (
            await session.execute(
                select(SeriesModel)
                .where(SeriesModel.store_id == store.id)
                .order_by(SeriesModel.name)
            )
        )
        .scalars()
        .all()
    )
    return [await _payload(session, row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_series(
    request: SeriesWrite,
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    series_slug = request.slug or slugify(request.name, allow_unicode=True) or "series"
    exists = await session.scalar(
        select(SeriesModel.id).where(
            SeriesModel.store_id == store.id, SeriesModel.slug == series_slug
        )
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Series slug already exists")
    row = SeriesModel(
        tenant_id=store.tenant_id,
        store_id=store.id,
        name=request.name.strip(),
        slug=series_slug,
        description=request.description,
        cover_image_url=request.cover_image_url,
        status=request.status,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await _invalidate_storefront(store.id)
    return await _payload(session, row)


@router.get("/{series_id}")
async def get_series(
    series_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    return await _payload(session, await _series_or_404(session, store.id, series_id))


@router.patch("/{series_id}")
async def update_series(
    request: SeriesPatch,
    series_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = await _series_or_404(session, store.id, series_id)
    for key, value in request.model_dump(exclude_unset=True).items():
        setattr(row, key, value.strip() if isinstance(value, str) else value)
    await session.commit()
    await session.refresh(row)
    await _invalidate_storefront(store.id)
    return await _payload(session, row)


@router.delete("/{series_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_series(
    series_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    row = await _series_or_404(session, store.id, series_id)
    await session.delete(row)
    await session.commit()
    await _invalidate_storefront(store.id)


@router.post("/{series_id}/products", status_code=status.HTTP_201_CREATED)
async def add_series_product(
    request: SeriesProductWrite,
    series_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = await _series_or_404(session, store.id, series_id)
    product = await session.scalar(
        select(ProductModel.id).where(
            ProductModel.id == request.product_id, ProductModel.store_id == store.id
        )
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    session.add(
        SeriesProductModel(
            tenant_id=store.tenant_id,
            series_id=series_id,
            product_id=request.product_id,
            volume_label=request.volume_label,
            position=request.position,
        )
    )
    await session.commit()
    await _invalidate_storefront(store.id)
    return await _payload(session, row)


@router.put("/{series_id}/order")
async def reorder_series(
    request: SeriesOrder,
    series_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = await _series_or_404(session, store.id, series_id)
    current = set(
        (
            await session.scalars(
                select(SeriesProductModel.product_id).where(
                    SeriesProductModel.series_id == series_id
                )
            )
        ).all()
    )
    if (
        len(request.product_ids) != len(set(request.product_ids))
        or set(request.product_ids) != current
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Order must contain every series product exactly once",
        )
    await session.execute(
        update(SeriesProductModel)
        .where(SeriesProductModel.series_id == series_id)
        .values(position=SeriesProductModel.position + 1_000_000)
    )
    for position, product_id in enumerate(request.product_ids, start=1):
        await session.execute(
            update(SeriesProductModel)
            .where(
                SeriesProductModel.series_id == series_id,
                SeriesProductModel.product_id == product_id,
            )
            .values(position=position)
        )
    await session.commit()
    await _invalidate_storefront(store.id)
    return await _payload(session, row)


@router.delete(
    "/{series_id}/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_series_product(
    series_id: Annotated[UUID, Path()],
    product_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await _series_or_404(session, store.id, series_id)
    result = await session.execute(
        delete(SeriesProductModel).where(
            SeriesProductModel.series_id == series_id,
            SeriesProductModel.product_id == product_id,
        )
    )
    if not result.rowcount:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Series product not found")
    await session.commit()
    await _invalidate_storefront(store.id)
