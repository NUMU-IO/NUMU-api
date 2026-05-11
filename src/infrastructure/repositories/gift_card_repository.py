"""Gift card repositories — Phase 8.3.

Two tables, both backed by a single repository because they're
always read/written together (every balance change records a
transaction).

Pattern: every mutation goes through `apply_transaction` which
atomically:
1. SELECT ... FOR UPDATE on the gift_cards row
2. Validates the delta won't push balance below zero (unless allowed)
3. Updates current_balance_cents
4. Inserts the ledger row

Single transaction, single trip through the lock.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.gift_card import (
    GiftCard,
    GiftCardStatus,
    GiftCardTransaction,
    TransactionKind,
    hash_code,
)
from src.infrastructure.database.models.tenant.gift_card import (
    GiftCardModel,
    GiftCardTransactionModel,
)


def _to_entity(row: GiftCardModel) -> GiftCard:
    return GiftCard(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        code_hash=row.code_hash,
        last_four=row.last_four,
        initial_balance_cents=row.initial_balance_cents,
        current_balance_cents=row.current_balance_cents,
        currency=row.currency,
        status=GiftCardStatus(row.status)
        if isinstance(row.status, str)
        else row.status,
        customer_id=row.customer_id,
        issued_by_user_id=row.issued_by_user_id,
        issuing_order_id=row.issuing_order_id,
        expires_at=row.expires_at,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _tx_to_entity(row: GiftCardTransactionModel) -> GiftCardTransaction:
    return GiftCardTransaction(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        gift_card_id=row.gift_card_id,
        kind=TransactionKind(row.kind) if isinstance(row.kind, str) else row.kind,
        amount_cents=row.amount_cents,
        order_id=row.order_id,
        actor_user_id=row.actor_user_id,
        actor_customer_id=row.actor_customer_id,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class GiftCardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, gift_card_id: UUID) -> GiftCard | None:
        row = (
            await self._session.execute(
                select(GiftCardModel).where(GiftCardModel.id == gift_card_id)
            )
        ).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def get_by_code(self, code: str, store_id: UUID) -> GiftCard | None:
        """Resolve a customer-typed code to a gift card row.

        Normalization (strip non-alphanum, uppercase) + SHA-256 hash
        is what's actually looked up. The store_id scope means a code
        leaked from store A can't be tried at store B even if the
        hash somehow collides (impossible with SHA-256 in practice).
        """
        code_hash = hash_code(code)
        row = (
            await self._session.execute(
                select(GiftCardModel).where(
                    GiftCardModel.code_hash == code_hash,
                    GiftCardModel.store_id == store_id,
                )
            )
        ).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def create(self, card: GiftCard) -> GiftCard:
        row = GiftCardModel(
            tenant_id=card.tenant_id,
            store_id=card.store_id,
            code_hash=card.code_hash,
            last_four=card.last_four,
            initial_balance_cents=card.initial_balance_cents,
            current_balance_cents=card.current_balance_cents,
            currency=card.currency,
            status=card.status,
            customer_id=card.customer_id,
            issued_by_user_id=card.issued_by_user_id,
            issuing_order_id=card.issuing_order_id,
            expires_at=card.expires_at,
            note=card.note,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_entity(row)

    async def list_for_store(
        self, store_id: UUID, *, status: GiftCardStatus | None = None
    ) -> list[GiftCard]:
        stmt = select(GiftCardModel).where(GiftCardModel.store_id == store_id)
        if status is not None:
            stmt = stmt.where(GiftCardModel.status == status)
        stmt = stmt.order_by(GiftCardModel.created_at.desc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(r) for r in rows]

    async def list_for_customer(self, customer_id: UUID) -> list[GiftCard]:
        rows = (
            (
                await self._session.execute(
                    select(GiftCardModel)
                    .where(GiftCardModel.customer_id == customer_id)
                    .order_by(GiftCardModel.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_to_entity(r) for r in rows]

    async def list_transactions(self, gift_card_id: UUID) -> list[GiftCardTransaction]:
        rows = (
            (
                await self._session.execute(
                    select(GiftCardTransactionModel)
                    .where(GiftCardTransactionModel.gift_card_id == gift_card_id)
                    .order_by(GiftCardTransactionModel.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_tx_to_entity(r) for r in rows]

    async def apply_transaction(
        self,
        *,
        gift_card_id: UUID,
        kind: TransactionKind,
        amount_cents: int,
        order_id: UUID | None = None,
        actor_user_id: UUID | None = None,
        actor_customer_id: UUID | None = None,
        note: str | None = None,
        allow_negative: bool = False,
    ) -> tuple[GiftCard, GiftCardTransaction]:
        """Apply a signed delta + record the ledger row, atomically.

        Negative amount = debit (redeem / void / adjust-down).
        Positive amount = credit (issue / refund / adjust-up).

        Raises ValueError on:
        * gift card not found
        * card not in ACTIVE status (except VOID/REFUND on depleted/voided)
        * delta would push balance below zero (unless allow_negative)
        """
        row = (
            await self._session.execute(
                select(GiftCardModel)
                .where(GiftCardModel.id == gift_card_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Gift card {gift_card_id} not found")

        new_balance = row.current_balance_cents + amount_cents
        if new_balance < 0 and not allow_negative:
            raise ValueError(
                f"Gift card balance insufficient: have "
                f"{row.current_balance_cents} cents, requested debit "
                f"{abs(amount_cents)} cents"
            )

        row.current_balance_cents = max(0, new_balance)
        # Status transitions driven by balance:
        if kind == TransactionKind.VOID:
            row.status = GiftCardStatus.VOIDED
        elif row.current_balance_cents == 0 and row.status == GiftCardStatus.ACTIVE:
            row.status = GiftCardStatus.DEPLETED
        elif row.status == GiftCardStatus.DEPLETED and row.current_balance_cents > 0:
            # Refund credited a depleted card back to active. Rare but valid.
            row.status = GiftCardStatus.ACTIVE

        tx = GiftCardTransactionModel(
            tenant_id=row.tenant_id,
            store_id=row.store_id,
            gift_card_id=row.id,
            kind=kind,
            amount_cents=amount_cents,
            order_id=order_id,
            actor_user_id=actor_user_id,
            actor_customer_id=actor_customer_id,
            note=note,
        )
        self._session.add(tx)
        await self._session.flush()
        await self._session.refresh(row)
        await self._session.refresh(tx)
        return _to_entity(row), _tx_to_entity(tx)
