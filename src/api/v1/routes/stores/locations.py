"""Merchant locations management — Phase 7.2.

Mounted at /stores/{store_id}/locations/

Endpoints:
  GET    /            — list this store's locations
  POST   /            — create a new location
  GET    /{id}        — single
  PUT    /{id}        — update
  DELETE /{id}        — hard-delete (only when no inventory rows
                       reference it; otherwise prefer `is_active=false`)

Address is JSONB and stored inline on the row — same Address value
type used throughout the platform.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.location import LocationModel
from src.infrastructure.repositories import StoreRepository

router = APIRouter(
    prefix="/{store_id}/locations",
    tags=["Locations"],
    dependencies=[Depends(verify_store_ownership)],
)


class LocationPayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    name_ar: str | None = None
    address: dict[str, Any] = Field(default_factory=dict)
    fulfills_orders: bool = True
    fulfills_pickup: bool = False
    pickup_instructions: str | None = None
    pickup_instructions_ar: str | None = None
    is_active: bool = True
    position: int = 0


class LocationResponse(LocationPayload):
    id: str
    created_at: str
    updated_at: str


def _to_response(row: LocationModel) -> LocationResponse:
    return LocationResponse(
        id=str(row.id),
        name=row.name,
        name_ar=row.name_ar,
        address=row.address or {},
        fulfills_orders=row.fulfills_orders,
        fulfills_pickup=row.fulfills_pickup,
        pickup_instructions=row.pickup_instructions,
        pickup_instructions_ar=row.pickup_instructions_ar,
        is_active=row.is_active,
        position=row.position,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.get(
    "",
    response_model=SuccessResponse[list[LocationResponse]],
    summary="List locations",
    operation_id="list_locations",
)
async def list_locations(store_id: UUID):
    async with AsyncSessionLocal() as session:
        stmt = (
            select(LocationModel)
            .where(LocationModel.store_id == store_id)
            .order_by(LocationModel.position.asc(), LocationModel.name.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()
    return SuccessResponse(
        data=[_to_response(r) for r in rows], message="Locations listed"
    )


@router.post(
    "",
    response_model=SuccessResponse[LocationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create location",
    operation_id="create_location",
)
async def create_location(
    store_id: UUID,
    body: LocationPayload,
    store_repo: StoreRepository = Depends(get_store_repository),
):
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )
    async with AsyncSessionLocal() as session:
        row = LocationModel(
            tenant_id=store.tenant_id,
            store_id=store_id,
            name=body.name,
            name_ar=body.name_ar,
            address=body.address,
            fulfills_orders=body.fulfills_orders,
            fulfills_pickup=body.fulfills_pickup,
            pickup_instructions=body.pickup_instructions,
            pickup_instructions_ar=body.pickup_instructions_ar,
            is_active=body.is_active,
            position=body.position,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return SuccessResponse(data=_to_response(row), message="Location created")


@router.get(
    "/{location_id}",
    response_model=SuccessResponse[LocationResponse],
    summary="Get location",
    operation_id="get_location",
)
async def get_location(store_id: UUID, location_id: UUID):
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(LocationModel).where(
                    LocationModel.id == location_id,
                    LocationModel.store_id == store_id,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    return SuccessResponse(data=_to_response(row), message="Location retrieved")


@router.put(
    "/{location_id}",
    response_model=SuccessResponse[LocationResponse],
    summary="Update location",
    operation_id="update_location",
)
async def update_location(store_id: UUID, location_id: UUID, body: LocationPayload):
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(LocationModel).where(
                    LocationModel.id == location_id,
                    LocationModel.store_id == store_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        row.name = body.name
        row.name_ar = body.name_ar
        row.address = body.address
        row.fulfills_orders = body.fulfills_orders
        row.fulfills_pickup = body.fulfills_pickup
        row.pickup_instructions = body.pickup_instructions
        row.pickup_instructions_ar = body.pickup_instructions_ar
        row.is_active = body.is_active
        row.position = body.position
        await session.commit()
        await session.refresh(row)
    return SuccessResponse(data=_to_response(row), message="Location updated")


@router.delete(
    "/{location_id}",
    response_model=SuccessResponse[dict[str, str]],
    summary="Delete location",
    operation_id="delete_location",
)
async def delete_location(store_id: UUID, location_id: UUID):
    """Hard-delete the row. Prefer `PUT { is_active: false }` when
    historical orders may still reference this location — the
    storefront hides inactive locations from pickers but historical
    rows can dereference the FK without nulling out."""
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(LocationModel).where(
                    LocationModel.id == location_id,
                    LocationModel.store_id == store_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
            )
        await session.delete(row)
        await session.commit()
    return SuccessResponse(data={"id": str(location_id)}, message="Location deleted")
