"""Storefront gift card routes — Phase 8.3.

Read-only balance check (no auth). Themes call this when the
customer types a code on the cart/checkout page to confirm the
card is real + has balance before they advance to payment. The
*actual* redemption happens server-side inside `POST /checkout`
(via `CheckoutRequest.gift_card_codes`), so this route exists
purely for the immediate "your balance is X EGP" feedback.

URL:
  GET /storefront/store/{store_id}/gift-cards/{code}

Anonymous, store-scoped lookup. Returns a sparse summary — never
the code or the hash. Invalid / expired / depleted cards return
404 with a generic message so attackers can't enumerate.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.api.responses import SuccessResponse
from src.application.services.gift_card_service import GiftCardService
from src.infrastructure.database.connection import AsyncSessionLocal

router = APIRouter()


class GiftCardBalanceResponse(BaseModel):
    last_four: str
    current_balance_cents: int
    currency: str
    expires_at: str | None = None


@router.get(
    "/gift-cards/{code}",
    response_model=SuccessResponse[GiftCardBalanceResponse],
    summary="Check gift card balance",
    operation_id="get_gift_card_balance",
)
async def get_balance(store_id: UUID, code: str):
    """Resolve a customer-typed code to its current balance.

    Returns 404 for any non-redeemable card (expired, depleted,
    voided, or not on this store) — keeps the response shape
    uniform so an attacker can't probe for valid codes.
    """
    async with AsyncSessionLocal() as session:
        svc = GiftCardService(session)
        card = await svc.get_by_code(code, store_id)
        if card is None or not card.is_redeemable():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Gift card not found or no longer redeemable.",
            )
    return SuccessResponse(
        data=GiftCardBalanceResponse(
            last_four=card.last_four,
            current_balance_cents=card.current_balance_cents,
            currency=card.currency,
            expires_at=card.expires_at.isoformat() if card.expires_at else None,
        ),
        message="Gift card balance retrieved",
    )
