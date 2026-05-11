"""Merchant inventory levels — Phase 8.2.

Mounted at /stores/{store_id}/inventory/levels/

Endpoints:
  GET    /                       — flat list of all levels in the store
  GET    /variant/{variant_id}   — levels for one variant
  GET    /location/{location_id} — levels at one location
  PUT    /{variant_id}/{location_id}  — set the level (idempotent)

PUT triggers a variant-total rollup so the variant's
`inventory_quantity` stays consistent without a JOIN on the
cart/checkout hot path.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.application.services.inventory_service import InventoryService
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories import StoreRepository

router = APIRouter(
    prefix="/{store_id}/inventory/levels",
    tags=["Inventory Levels"],
    dependencies=[Depends(verify_store_ownership)],
)


class InventoryLevelResponse(BaseModel):
    id: str
    variant_id: str
    location_id: str
    available: int
    reserved: int


class SetLevelRequest(BaseModel):
    available: int = Field(ge=0)


def _to_response(level) -> InventoryLevelResponse:
    return InventoryLevelResponse(
        id=str(level.id),
        variant_id=str(level.variant_id),
        location_id=str(level.location_id),
        available=level.available,
        reserved=level.reserved,
    )


@router.get(
    "",
    response_model=SuccessResponse[list[InventoryLevelResponse]],
    summary="List all inventory levels for a store",
    operation_id="list_store_inventory_levels",
)
async def list_levels(store_id: UUID):
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        levels = await svc._levels.list_for_store(store_id)
    return SuccessResponse(
        data=[_to_response(level) for level in levels],
        message="Inventory levels listed",
    )


@router.get(
    "/variant/{variant_id}",
    response_model=SuccessResponse[list[InventoryLevelResponse]],
    summary="List inventory levels for a variant",
    operation_id="list_variant_inventory_levels",
)
async def list_levels_for_variant(store_id: UUID, variant_id: UUID):
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        levels = await svc._levels.list_for_variant(variant_id)
    # Filter to this store for safety even though variant FK already
    # scopes it — the route's path scope is the store, not the variant.
    levels = [level for level in levels if level.store_id == store_id]
    return SuccessResponse(
        data=[_to_response(level) for level in levels],
        message="Variant inventory listed",
    )


@router.get(
    "/location/{location_id}",
    response_model=SuccessResponse[list[InventoryLevelResponse]],
    summary="List inventory levels at a location",
    operation_id="list_location_inventory_levels",
)
async def list_levels_for_location(store_id: UUID, location_id: UUID):
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        levels = await svc._levels.list_for_location(location_id)
    levels = [level for level in levels if level.store_id == store_id]
    return SuccessResponse(
        data=[_to_response(level) for level in levels],
        message="Location inventory listed",
    )


@router.put(
    "/{variant_id}/{location_id}",
    response_model=SuccessResponse[InventoryLevelResponse],
    summary="Set inventory level at a location",
    operation_id="set_inventory_level",
)
async def set_level(
    store_id: UUID,
    variant_id: UUID,
    location_id: UUID,
    body: SetLevelRequest,
    store_repo: StoreRepository = Depends(get_store_repository),
):
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        new_total = await svc.set_level(
            tenant_id=store.tenant_id,
            store_id=store_id,
            variant_id=variant_id,
            location_id=location_id,
            available=body.available,
        )
        await session.commit()
        # Re-read the level for the response
        level = await svc._levels.get(variant_id=variant_id, location_id=location_id)
        await session.commit()
    if level is None:
        raise HTTPException(status_code=500, detail="Level upsert did not return a row")
    resp = _to_response(level)
    return SuccessResponse(
        data=resp,
        message=f"Level set; variant total is now {new_total}.",
    )
