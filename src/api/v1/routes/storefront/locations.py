"""Storefront pickup-locations route — Phase 7.2.

Public read-only. The storefront checkout's shipping step calls this
to render an "I'll pick up in-store" option alongside the regular
shipping rates. Themes that drive checkout via `useCheckout()` can
also read this and render a custom pickup picker (e.g. on a map).

URL:
  GET /storefront/store/{store_id}/pickup-locations
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from src.api.responses import SuccessResponse
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.location import LocationModel

router = APIRouter()


class PickupLocationSummary(BaseModel):
    id: str
    name: str
    name_ar: str | None = None
    address: dict
    pickup_instructions: str | None = None
    pickup_instructions_ar: str | None = None


@router.get(
    "/pickup-locations",
    response_model=SuccessResponse[list[PickupLocationSummary]],
    summary="List in-store pickup locations",
    operation_id="list_pickup_locations",
)
async def list_pickup_locations(store_id: UUID):
    """Return enabled pickup-capable locations for this store, sorted
    by display position. Empty list when the merchant hasn't enabled
    pickup or hasn't configured any locations yet — themes branch on
    `length === 0` to hide the pickup tab entirely.
    """
    async with AsyncSessionLocal() as session:
        stmt = (
            select(LocationModel)
            .where(
                LocationModel.store_id == store_id,
                LocationModel.is_active.is_(True),
                LocationModel.fulfills_pickup.is_(True),
            )
            .order_by(LocationModel.position.asc(), LocationModel.name.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()

    return SuccessResponse(
        data=[
            PickupLocationSummary(
                id=str(r.id),
                name=r.name,
                name_ar=r.name_ar,
                address=r.address or {},
                pickup_instructions=r.pickup_instructions,
                pickup_instructions_ar=r.pickup_instructions_ar,
            )
            for r in rows
        ],
        message="Pickup locations listed",
    )
