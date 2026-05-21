"""Storefront saved-cards routes — Phase 7.5.

Customer-facing list/delete for previously-saved cards, so the
storefront checkout payment step can render "Pay with •••• 4242"
options instead of forcing the full new-card capture form every
time.

URLs:
  GET    /storefront/me/saved-cards?store_id={id}  → list
  DELETE /storefront/me/saved-cards/{id}           → soft-delete (is_active=false)

The actual charge against a saved card happens inside POST /checkout
when the request body carries `saved_payment_method_id`. Storage of
new tokens happens automatically after a successful first charge on
that gateway — this route is read-only on creation.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from src.api.dependencies.auth import get_current_customer
from src.api.responses import SuccessResponse
from src.core.entities.customer import Customer
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.saved_payment_method import (
    SavedPaymentMethodModel,
)

router = APIRouter()


class SavedCardSummary(BaseModel):
    id: str
    gateway: str
    display_name: str | None
    card_brand: str | None
    last_four: str | None
    created_at: str


@router.get(
    "/saved-cards",
    response_model=SuccessResponse[list[SavedCardSummary]],
    summary="List saved cards",
    operation_id="list_saved_cards",
)
async def list_saved_cards(
    customer: Annotated[Customer, Depends(get_current_customer)],
    store_id: Annotated[UUID, Query(description="Store ID")],
):
    """Return the active saved cards for this customer + store.

    Cards are scoped by (customer_id, store_id) — a saved card from
    store A is never visible at store B's checkout. Deleted (or
    rotated-out) cards have `is_active=false` and don't appear here.
    """
    async with AsyncSessionLocal() as session:
        stmt = (
            select(SavedPaymentMethodModel)
            .where(
                SavedPaymentMethodModel.customer_id == customer.id,
                SavedPaymentMethodModel.store_id == store_id,
                SavedPaymentMethodModel.is_active.is_(True),
            )
            .order_by(SavedPaymentMethodModel.created_at.desc())
        )
        rows = (await session.execute(stmt)).scalars().all()

    return SuccessResponse(
        data=[
            SavedCardSummary(
                id=str(r.id),
                gateway=r.gateway,
                display_name=r.display_name,
                card_brand=r.card_brand,
                last_four=r.last_four,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ],
        message="Saved cards listed",
    )


@router.delete(
    "/saved-cards/{card_id}",
    response_model=SuccessResponse[dict[str, str]],
    summary="Delete a saved card",
    operation_id="delete_saved_card",
)
async def delete_saved_card(
    card_id: UUID,
    customer: Annotated[Customer, Depends(get_current_customer)],
):
    """Soft-delete a saved card. The token row stays in the DB for
    refund/audit purposes; the customer just won't see it offered at
    checkout again."""
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(SavedPaymentMethodModel).where(
                    SavedPaymentMethodModel.id == card_id,
                    SavedPaymentMethodModel.customer_id == customer.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Card not found"
            )
        row.is_active = False
        await session.commit()

    return SuccessResponse(data={"id": str(card_id)}, message="Card removed")
