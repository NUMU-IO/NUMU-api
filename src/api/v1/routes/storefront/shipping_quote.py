"""Storefront shipping rate quotes.

POST /api/v1/storefront/store/{store_id}/shipping/quote
No auth required — called from the checkout page.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.interfaces.services.shipping_service import Parcel, ShippingAddress

logger = logging.getLogger(__name__)
router = APIRouter()


class ShippingQuoteRequest(BaseModel):
    destination_city: str = Field(description="Destination city / governorate")
    destination_country: str = Field("EG", description="ISO country code")
    weight_kg: float = Field(1.0, description="Package weight in kg")


class ShippingQuoteOption(BaseModel):
    carrier: str
    service: str
    amount_cents: int
    currency: str
    estimated_days: int | None


@router.post(
    "/shipping/quote",
    response_model=SuccessResponse[list[ShippingQuoteOption]],
    summary="Get shipping rate quotes for checkout",
    operation_id="get_shipping_quote",
)
async def get_shipping_quote(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: ShippingQuoteRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return available shipping rates for a destination.

    Called from the storefront checkout page. Demo tenants get
    simulated rates; real tenants get live carrier quotes.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models import StoreModel
    from src.infrastructure.database.models.public.tenant import TenantModel

    # Resolve store + tenant
    store = (
        await db.execute(select(StoreModel).where(StoreModel.id == store_id))
    ).scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")

    tenant = (
        await db.execute(select(TenantModel).where(TenantModel.id == store.tenant_id))
    ).scalar_one_or_none()

    # Demo tenants get simulated rates
    if tenant and tenant.is_demo:
        return SuccessResponse(
            data=[
                ShippingQuoteOption(
                    carrier="Bosta",
                    service="Standard",
                    amount_cents=5000,
                    currency="EGP",
                    estimated_days=3,
                ),
                ShippingQuoteOption(
                    carrier="Bosta",
                    service="Express",
                    amount_cents=8000,
                    currency="EGP",
                    estimated_days=1,
                ),
            ],
            message="Simulated demo rates",
        )

    # Real tenants — try Bosta first (most common Egyptian carrier)
    rates = []
    try:
        from src.infrastructure.external_services.bosta.shipping_service import (
            BostaShippingService,
        )

        bosta = BostaShippingService()
        origin = ShippingAddress(name="Store", street1="", city="Cairo", country="EG")
        dest = ShippingAddress(
            name="Customer",
            street1="",
            city=request.destination_city,
            country=request.destination_country,
        )
        parcel = Parcel(length=30, width=20, height=15, weight=request.weight_kg)

        bosta_rates = await bosta.get_rates(origin, dest, parcel)
        for r in bosta_rates:
            rates.append(
                ShippingQuoteOption(
                    carrier=r.carrier,
                    service=r.service,
                    amount_cents=r.amount,
                    currency=r.currency,
                    estimated_days=r.estimated_days,
                )
            )
    except Exception:
        logger.warning("bosta_rate_fetch_failed", exc_info=True)

    # Fallback flat rates if carrier API fails
    if not rates:
        rates = [
            ShippingQuoteOption(
                carrier="Flat Rate",
                service="Standard",
                amount_cents=5000,
                currency="EGP",
                estimated_days=3,
            ),
        ]

    return SuccessResponse(data=rates, message="Shipping rates retrieved")
