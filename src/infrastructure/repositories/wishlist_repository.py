"""Wishlist repository (Phase 4.5).

Idempotent upsert + list + delete. Owner is either customer_id or
session_id — never both. The route layer enforces that via the
existing cart_owner dependency pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.wishlist import WishlistItem
from src.infrastructure.database.models.tenant.wishlist import WishlistItemModel


def _to_entity(row: WishlistItemModel) -> WishlistItem:
    return WishlistItem(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        customer_id=row.customer_id,
        session_id=row.session_id,
        product_id=row.product_id,
        variant_id=row.variant_id,
        added_at=row.added_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class WishlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        customer_id: UUID | None,
        session_id: str | None,
        product_id: UUID,
        variant_id: UUID | None,
    ) -> WishlistItem:
        """Idempotent insert. Re-adding the same target is a no-op
        that returns the existing row.

        Note on owner identity: customer_id XOR session_id should be
        non-null. The route enforces that; the repo doesn't, so the
        unique constraint covers all four combinations cleanly.
        """
        stmt = (
            pg_insert(WishlistItemModel)
            .values(
                tenant_id=tenant_id,
                store_id=store_id,
                customer_id=customer_id,
                session_id=session_id,
                product_id=product_id,
                variant_id=variant_id,
                added_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_wishlist_target")
            .returning(WishlistItemModel)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            existing = await self._session.execute(
                select(WishlistItemModel).where(
                    WishlistItemModel.customer_id == customer_id,
                    WishlistItemModel.session_id == session_id,
                    WishlistItemModel.product_id == product_id,
                    WishlistItemModel.variant_id == variant_id,
                )
            )
            row = existing.scalar_one()
        await self._session.commit()
        return _to_entity(row)

    async def remove(
        self,
        *,
        customer_id: UUID | None,
        session_id: str | None,
        product_id: UUID,
        variant_id: UUID | None,
    ) -> bool:
        """Drop a single (owner, product, variant) entry.

        Returns True when a row was removed; False otherwise (idempotent
        for repeat-removes — same shape as the cart's remove endpoint).
        """
        stmt = (
            delete(WishlistItemModel)
            .where(
                and_(
                    WishlistItemModel.customer_id == customer_id,
                    WishlistItemModel.session_id == session_id,
                    WishlistItemModel.product_id == product_id,
                    WishlistItemModel.variant_id == variant_id,
                )
            )
            .returning(WishlistItemModel.id)
        )
        result = await self._session.execute(stmt)
        deleted = result.scalar_one_or_none() is not None
        await self._session.commit()
        return deleted

    async def list_for_owner(
        self,
        *,
        customer_id: UUID | None,
        session_id: str | None,
        store_id: UUID,
    ) -> list[WishlistItem]:
        if customer_id is None and session_id is None:
            return []
        clauses = [WishlistItemModel.store_id == store_id]
        if customer_id is not None:
            clauses.append(WishlistItemModel.customer_id == customer_id)
        else:
            clauses.append(WishlistItemModel.session_id == session_id)
        result = await self._session.execute(
            select(WishlistItemModel)
            .where(and_(*clauses))
            .order_by(WishlistItemModel.added_at.desc())
        )
        return [_to_entity(r) for r in result.scalars().all()]

    async def merge_session_to_customer(
        self,
        *,
        session_id: str,
        customer_id: UUID,
    ) -> int:
        """Move guest-session wishlist rows under a customer on login.

        Returns the number of rows reassigned. Conflicts (customer
        already has the same product/variant) silently drop the
        session-side row — we don't want to re-add an identical entry.
        """
        # Two-step to handle the conflict:
        #   1. Reassign session→customer for items the customer doesn't
        #      already have.
        #   2. Delete leftover session-only duplicates.
        # A single UPDATE … ON CONFLICT DO NOTHING would be tidier but
        # Postgres doesn't support that on UPDATE. We pay the extra
        # round-trip; the merge runs once per login.
        candidates = await self._session.execute(
            select(WishlistItemModel).where(
                WishlistItemModel.session_id == session_id,
                WishlistItemModel.customer_id.is_(None),
            )
        )
        rows = list(candidates.scalars().all())
        moved = 0
        for row in rows:
            # Does the customer already have this (product, variant)?
            dup = await self._session.execute(
                select(WishlistItemModel.id).where(
                    WishlistItemModel.customer_id == customer_id,
                    WishlistItemModel.product_id == row.product_id,
                    WishlistItemModel.variant_id == row.variant_id,
                )
            )
            if dup.scalar_one_or_none():
                # Drop the session-side dup; customer's row wins.
                await self._session.delete(row)
                continue
            row.customer_id = customer_id
            row.session_id = None
            moved += 1
        await self._session.commit()
        return moved
