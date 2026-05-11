"""Merchant gift card management — Phase 8.3.

Mounted at /stores/{store_id}/gift-cards/

Endpoints:
  GET    /                  — list cards (optional ?status= filter)
  POST   /                  — issue a new card. Response carries
                              the **plaintext code** ONCE — the hub
                              must surface it to the merchant
                              immediately (clipboard copy, email
                              link, print) because it's
                              non-recoverable from the DB after this.
  GET    /{id}              — single card detail
  GET    /{id}/transactions — ledger view
  POST   /{id}/void         — void the card (zeros remaining balance)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.application.services.gift_card_service import GiftCardService
from src.core.entities.gift_card import (
    GiftCard,
    GiftCardStatus,
    GiftCardTransaction,
)
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories import StoreRepository

router = APIRouter(
    prefix="/{store_id}/gift-cards",
    tags=["Gift Cards"],
    dependencies=[Depends(verify_store_ownership)],
)


# ── Schemas ──────────────────────────────────────────────────────


class IssueGiftCardRequest(BaseModel):
    initial_balance_cents: int = Field(gt=0, le=10_000_000)
    currency: str = Field(min_length=3, max_length=3, default="EGP")
    customer_id: UUID | None = None
    expires_at: datetime | None = None
    note: str | None = Field(None, max_length=500)


class GiftCardResponse(BaseModel):
    id: str
    last_four: str
    initial_balance_cents: int
    current_balance_cents: int
    currency: str
    status: str
    customer_id: str | None = None
    issued_by_user_id: str | None = None
    issuing_order_id: str | None = None
    expires_at: str | None = None
    note: str | None = None
    created_at: str
    updated_at: str


class IssuedGiftCardResponse(BaseModel):
    """Response returned ONLY on issue. Includes plaintext code."""

    card: GiftCardResponse
    code: str = Field(
        description=(
            "Plaintext code — only available immediately after issue. "
            "Cannot be recovered from the DB once this response is sent. "
            "Display to merchant for delivery to customer."
        )
    )


class GiftCardTransactionResponse(BaseModel):
    id: str
    kind: str
    amount_cents: int
    order_id: str | None = None
    actor_user_id: str | None = None
    actor_customer_id: str | None = None
    note: str | None = None
    created_at: str


class VoidRequest(BaseModel):
    note: str | None = Field(None, max_length=500)


def _card_to_response(card: GiftCard) -> GiftCardResponse:
    return GiftCardResponse(
        id=str(card.id),
        last_four=card.last_four,
        initial_balance_cents=card.initial_balance_cents,
        current_balance_cents=card.current_balance_cents,
        currency=card.currency,
        status=card.status.value,
        customer_id=str(card.customer_id) if card.customer_id else None,
        issued_by_user_id=str(card.issued_by_user_id)
        if card.issued_by_user_id
        else None,
        issuing_order_id=str(card.issuing_order_id) if card.issuing_order_id else None,
        expires_at=card.expires_at.isoformat() if card.expires_at else None,
        note=card.note,
        created_at=card.created_at.isoformat() if card.created_at else "",
        updated_at=card.updated_at.isoformat() if card.updated_at else "",
    )


def _tx_to_response(tx: GiftCardTransaction) -> GiftCardTransactionResponse:
    return GiftCardTransactionResponse(
        id=str(tx.id),
        kind=tx.kind.value,
        amount_cents=tx.amount_cents,
        order_id=str(tx.order_id) if tx.order_id else None,
        actor_user_id=str(tx.actor_user_id) if tx.actor_user_id else None,
        actor_customer_id=str(tx.actor_customer_id) if tx.actor_customer_id else None,
        note=tx.note,
        created_at=tx.created_at.isoformat() if tx.created_at else "",
    )


# ── Routes ───────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[list[GiftCardResponse]],
    summary="List gift cards",
    operation_id="list_gift_cards",
)
async def list_cards(
    store_id: UUID,
    status_filter: GiftCardStatus | None = Query(None, alias="status"),
):
    async with AsyncSessionLocal() as session:
        svc = GiftCardService(session)
        cards = await svc._repo.list_for_store(store_id, status=status_filter)
    return SuccessResponse(
        data=[_card_to_response(c) for c in cards],
        message="Gift cards listed",
    )


@router.post(
    "",
    response_model=SuccessResponse[IssuedGiftCardResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new gift card",
    operation_id="issue_gift_card",
)
async def issue_card(
    store_id: UUID,
    body: IssueGiftCardRequest,
    user_id: UUID = Depends(get_current_user_id),
    store_repo: StoreRepository = Depends(get_store_repository),
):
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )
    async with AsyncSessionLocal() as session:
        svc = GiftCardService(session)
        try:
            card, code = await svc.issue(
                tenant_id=store.tenant_id,
                store_id=store_id,
                amount_cents=body.initial_balance_cents,
                currency=body.currency,
                customer_id=body.customer_id,
                issued_by_user_id=user_id,
                expires_at=body.expires_at,
                note=body.note,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
            ) from e
        await session.commit()
    return SuccessResponse(
        data=IssuedGiftCardResponse(card=_card_to_response(card), code=code),
        message=(
            "Gift card issued. The plaintext code is in the response — "
            "deliver it to the customer now; it cannot be recovered later."
        ),
    )


@router.get(
    "/{gift_card_id}",
    response_model=SuccessResponse[GiftCardResponse],
    summary="Get gift card",
    operation_id="get_gift_card",
)
async def get_card(store_id: UUID, gift_card_id: UUID):
    async with AsyncSessionLocal() as session:
        svc = GiftCardService(session)
        card = await svc._repo.get_by_id(gift_card_id)
    if card is None or card.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Gift card not found"
        )
    return SuccessResponse(data=_card_to_response(card), message="Gift card retrieved")


@router.get(
    "/{gift_card_id}/transactions",
    response_model=SuccessResponse[list[GiftCardTransactionResponse]],
    summary="List gift card transactions",
    operation_id="list_gift_card_transactions",
)
async def list_card_transactions(store_id: UUID, gift_card_id: UUID):
    async with AsyncSessionLocal() as session:
        svc = GiftCardService(session)
        card = await svc._repo.get_by_id(gift_card_id)
        if card is None or card.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Gift card not found"
            )
        txs = await svc.list_transactions(gift_card_id)
    return SuccessResponse(
        data=[_tx_to_response(t) for t in txs],
        message="Transactions listed",
    )


@router.post(
    "/{gift_card_id}/void",
    response_model=SuccessResponse[GiftCardResponse],
    summary="Void a gift card (zero remaining balance)",
    operation_id="void_gift_card",
)
async def void_card(
    store_id: UUID,
    gift_card_id: UUID,
    body: VoidRequest,
    user_id: UUID = Depends(get_current_user_id),
):
    async with AsyncSessionLocal() as session:
        svc = GiftCardService(session)
        card = await svc._repo.get_by_id(gift_card_id)
        if card is None or card.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Gift card not found"
            )
        try:
            updated = await svc.void(
                gift_card_id=gift_card_id,
                actor_user_id=user_id,
                note=body.note,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
            ) from e
        await session.commit()
    return SuccessResponse(data=_card_to_response(updated), message="Gift card voided")
