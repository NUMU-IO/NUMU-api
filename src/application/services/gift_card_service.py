"""Gift card service — Phase 8.3.

Two operations the route layer needs to compose:

* `issue` — generate a fresh code + persist the card + record the
  initial-balance ledger row. Returns the **plaintext code** for the
  hub to email/SMS the customer ONCE — after this, only the hash
  lives in the DB and the code can't be recovered server-side
  (matching how every gift-card-issuing platform works; a customer
  who loses their code has to ask the merchant to void + reissue).

* `redeem` — debit a card at checkout. Validates redeemability +
  applies the ledger transaction. Caller (checkout flow) is
  responsible for converting the debited amount into a reduction
  of `amount_due` (gift cards are TENDER — they don't reduce the
  taxable subtotal, just the amount the gateway charges).

Code format: 16 chars, Crockford base32 alphabet
(`ABCDEFGHJKMNPQRSTVWXYZ23456789`) — drops 0/O/1/I/L/U for clarity.
Displayed as `GC-XXXX-XXXX-XXXX` but stored normalized.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.gift_card import (
    GiftCard,
    GiftCardStatus,
    GiftCardTransaction,
    TransactionKind,
    hash_code,
    normalize_code,
)
from src.infrastructure.database.models.tenant.gift_card import GiftCardModel
from src.infrastructure.repositories.gift_card_repository import (
    GiftCardRepository,
)

# Crockford base32 minus visually-ambiguous chars (0/O, 1/I/L, U).
# Customers WILL transcribe these wrong in a coffee shop and Crockford
# is the standard mitigation.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_CODE_LENGTH = 16


def generate_code() -> str:
    """Return a fresh 16-char gift card code in `GC-XXXX-XXXX-XXXX-XXXX`
    display format. The persisted hash uses the normalized form."""
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    # Group in fours so customers can dictate it over the phone without
    # losing track of which char they're on.
    chunks = [body[i : i + 4] for i in range(0, _CODE_LENGTH, 4)]
    return "GC-" + "-".join(chunks)


class GiftCardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = GiftCardRepository(session)

    async def issue(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        amount_cents: int,
        currency: str,
        customer_id: UUID | None = None,
        issued_by_user_id: UUID | None = None,
        issuing_order_id: UUID | None = None,
        expires_at: datetime | None = None,
        note: str | None = None,
    ) -> tuple[GiftCard, str]:
        """Mint a new card. Returns `(card, plaintext_code)` — the
        plaintext is only available here and must be delivered to the
        customer in the same response. After this returns, the code
        is non-recoverable from the DB.
        """
        if amount_cents <= 0:
            raise ValueError("Gift card initial balance must be > 0")

        # Retry up to 3x on the astronomically-unlikely event of a
        # SHA-256 collision. The UNIQUE constraint on code_hash is the
        # ultimate guard; this pre-check just gives us a clean error
        # message instead of the constraint-violation 500.
        code = ""
        code_hash = ""
        normalized = ""
        for _ in range(3):
            code = generate_code()
            normalized = normalize_code(code)
            code_hash = hash_code(code)
            existing = (
                await self._session.execute(
                    select(GiftCardModel.id).where(GiftCardModel.code_hash == code_hash)
                )
            ).first()
            if existing is None:
                break
        else:
            raise RuntimeError("Gift card code generation failed (3 collisions)")

        # Create with balance=0; the ISSUE transaction credits the
        # initial amount. This keeps the ledger invariant
        # SUM(transactions.amount) == current_balance_cents intact.
        card = GiftCard(
            tenant_id=tenant_id,
            store_id=store_id,
            code_hash=code_hash,
            last_four=normalized[-4:],
            initial_balance_cents=amount_cents,
            current_balance_cents=0,
            currency=currency,
            status=GiftCardStatus.ACTIVE,
            customer_id=customer_id,
            issued_by_user_id=issued_by_user_id,
            issuing_order_id=issuing_order_id,
            expires_at=expires_at,
            note=note,
        )
        created = await self._repo.create(card)
        credited, _ = await self._repo.apply_transaction(
            gift_card_id=created.id,
            kind=TransactionKind.ISSUE,
            amount_cents=amount_cents,
            actor_user_id=issued_by_user_id,
            note=f"issued: {amount_cents} {currency}",
        )
        return credited, code

    async def get_by_code(self, code: str, store_id: UUID) -> GiftCard | None:
        """Customer-typed code → card. Returns None on miss."""
        return await self._repo.get_by_code(code, store_id)

    async def redeem(
        self,
        *,
        gift_card_id: UUID,
        amount_cents: int,
        order_id: UUID,
        actor_customer_id: UUID | None = None,
    ) -> GiftCard:
        """Debit a card at checkout. Returns the updated card row.

        Caller (checkout flow) computes `amount_cents` as
        `min(card.current_balance_cents, order_amount_due_cents)`
        — gift cards reduce amount_due but can't make it negative,
        and the customer pays the remainder via the chosen gateway.

        Raises ValueError when the card is not redeemable (depleted,
        expired, voided) or the amount exceeds the balance.
        """
        if amount_cents <= 0:
            raise ValueError("Redemption amount must be > 0")
        card = await self._repo.get_by_id(gift_card_id)
        if card is None:
            raise ValueError(f"Gift card {gift_card_id} not found")
        if not card.is_redeemable():
            raise ValueError(
                f"Gift card not redeemable (status={card.status.value}, "
                f"balance={card.current_balance_cents}, expires_at={card.expires_at})"
            )
        if amount_cents > card.current_balance_cents:
            raise ValueError(
                f"Redemption {amount_cents} exceeds balance "
                f"{card.current_balance_cents}"
            )
        updated, _tx = await self._repo.apply_transaction(
            gift_card_id=gift_card_id,
            kind=TransactionKind.REDEEM,
            amount_cents=-amount_cents,
            order_id=order_id,
            actor_customer_id=actor_customer_id,
        )
        return updated

    async def refund(
        self,
        *,
        gift_card_id: UUID,
        amount_cents: int,
        order_id: UUID,
        note: str | None = None,
    ) -> GiftCard:
        """Credit a card back from an order refund. Used by the
        refund flow when the customer originally paid with a gift
        card — the funds go back to the same card."""
        if amount_cents <= 0:
            raise ValueError("Refund amount must be > 0")
        updated, _tx = await self._repo.apply_transaction(
            gift_card_id=gift_card_id,
            kind=TransactionKind.REFUND,
            amount_cents=amount_cents,
            order_id=order_id,
            note=note,
        )
        return updated

    async def void(
        self,
        *,
        gift_card_id: UUID,
        actor_user_id: UUID | None = None,
        note: str | None = None,
    ) -> GiftCard:
        """Zero out the remaining balance + flip to VOIDED status.
        Used for fraud / lost cards / merchant cancellation. The
        ledger row records the debited amount so the audit trail
        is complete."""
        card = await self._repo.get_by_id(gift_card_id)
        if card is None:
            raise ValueError(f"Gift card {gift_card_id} not found")
        remaining = card.current_balance_cents
        updated, _tx = await self._repo.apply_transaction(
            gift_card_id=gift_card_id,
            kind=TransactionKind.VOID,
            amount_cents=-remaining if remaining > 0 else 0,
            actor_user_id=actor_user_id,
            note=note or "voided by merchant",
            allow_negative=False,
        )
        return updated

    async def list_transactions(self, gift_card_id: UUID) -> list[GiftCardTransaction]:
        return await self._repo.list_transactions(gift_card_id)
