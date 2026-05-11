"""Repository for back-in-stock product subscriptions (Phase 3.5).

Thin wrapper over the SQLAlchemy model — the call sites (storefront
notify endpoint, Celery sweep task) only need a couple of operations:

    upsert_subscription   — idempotent subscribe (handles double-click)
    list_pending_for_store — sweep input
    mark_notified         — sweep output

We use `INSERT ... ON CONFLICT DO NOTHING` for the upsert so a returning
visitor clicking "Notify me" twice doesn't fail with a unique-constraint
error. The unique constraint is on (product_id, variant_id, email).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.product_subscription import ProductSubscription
from src.infrastructure.database.models.tenant.product_subscription import (
    ProductSubscriptionModel,
)


def _to_entity(row: ProductSubscriptionModel) -> ProductSubscription:
    return ProductSubscription(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        product_id=row.product_id,
        variant_id=row.variant_id,
        email=row.email,
        notified_at=row.notified_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class ProductSubscriptionRepository:
    """Persistence for ProductSubscription rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_subscription(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        product_id: UUID,
        variant_id: UUID | None,
        email: str,
    ) -> ProductSubscription:
        """Subscribe (or no-op if already subscribed and pending).

        Idempotent: a second click for the same (product, variant, email)
        target keeps the existing row. We do NOT clear `notified_at` on
        re-subscribe — a customer who already received their notification
        and re-subscribes for the next stockout creates a fresh row at
        the application layer (the storefront route checks for an
        unsent row first).
        """
        normalized_email = email.strip().lower()
        stmt = (
            pg_insert(ProductSubscriptionModel)
            .values(
                tenant_id=tenant_id,
                store_id=store_id,
                product_id=product_id,
                variant_id=variant_id,
                email=normalized_email,
            )
            .on_conflict_do_nothing(constraint="uq_product_subscription_target")
            .returning(ProductSubscriptionModel)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            # Conflict path — fetch the existing row so the caller gets
            # a consistent return value regardless of insert vs no-op.
            existing = await self._session.execute(
                select(ProductSubscriptionModel).where(
                    ProductSubscriptionModel.product_id == product_id,
                    ProductSubscriptionModel.variant_id == variant_id,
                    ProductSubscriptionModel.email == normalized_email,
                )
            )
            row = existing.scalar_one()
        await self._session.commit()
        return _to_entity(row)

    async def list_pending_for_product(
        self,
        product_id: UUID,
        limit: int = 1000,
    ) -> list[ProductSubscription]:
        """Return un-notified subscriptions for a product (sweep input).

        Caller is the back-in-stock Celery task; it scans products that
        flipped to in-stock and pulls their pending subscribers in
        batches.
        """
        stmt = (
            select(ProductSubscriptionModel)
            .where(
                ProductSubscriptionModel.product_id == product_id,
                ProductSubscriptionModel.notified_at.is_(None),
            )
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_entity(r) for r in rows]

    async def mark_notified(self, subscription_ids: list[UUID]) -> int:
        """Stamp `notified_at = now()` on a batch of subscriptions.

        Returns the number of rows updated. Idempotent: re-running with
        already-notified ids is a no-op (the WHERE filter excludes them).
        """
        if not subscription_ids:
            return 0
        stmt = (
            update(ProductSubscriptionModel)
            .where(
                ProductSubscriptionModel.id.in_(subscription_ids),
                ProductSubscriptionModel.notified_at.is_(None),
            )
            .values(notified_at=datetime.now(UTC))
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount or 0
