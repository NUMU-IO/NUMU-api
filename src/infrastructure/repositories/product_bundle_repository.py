"""Product bundle (Frequently Bought Together) repository.

Handles all database operations for the product_bundles table.
Follows the same patterns established by UpsellRuleRepository.
"""

import logging
from uuid import UUID

from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.infrastructure.database.models.tenant.product_bundle import (
    ProductBundleModel,
)

logger = logging.getLogger(__name__)


class ProductBundleRepository:
    """Repository for product bundle CRUD and queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Create ────────────────────────────────────────────────────────────

    async def create(self, data: dict) -> ProductBundleModel:
        """Create a single bundle association."""
        model = ProductBundleModel(**data)
        self.session.add(model)
        await self.session.flush()
        return model

    async def bulk_create(self, items: list[dict]) -> list[ProductBundleModel]:
        """Create multiple bundle associations in one flush.

        Re-queries the newly flushed rows with `bundled_product` eager-loaded
        via `selectinload`. Skipping this causes a `MissingGreenlet` error
        the moment the response serializer (`_bundle_response`) touches
        `bundle.bundled_product`, because the relationship would otherwise
        lazy-load *outside* the async session context.
        """
        models = [ProductBundleModel(**item) for item in items]
        self.session.add_all(models)
        await self.session.flush()
        if not models:
            return []
        ids = [m.id for m in models]
        result = await self.session.execute(
            select(ProductBundleModel)
            .options(selectinload(ProductBundleModel.bundled_product))
            .where(ProductBundleModel.id.in_(ids))
            .order_by(ProductBundleModel.position.asc())
        )
        return list(result.scalars().all())

    # ── Read ──────────────────────────────────────────────────────────────

    async def get_by_id(
        self, bundle_id: UUID, store_id: UUID | None = None
    ) -> ProductBundleModel | None:
        """Get a single bundle by ID, optionally scoped to store."""
        query = (
            select(ProductBundleModel)
            .options(selectinload(ProductBundleModel.bundled_product))
            .where(ProductBundleModel.id == bundle_id)
        )
        if store_id is not None:
            query = query.where(ProductBundleModel.store_id == store_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_by_primary_product(
        self,
        store_id: UUID,
        primary_product_id: UUID,
        active_only: bool = False,
    ) -> list[ProductBundleModel]:
        """List all bundles for a given primary product (dashboard + storefront).

        Returns bundles ordered by position ASC for consistent display.
        `bundled_product` is eager-loaded so `_bundle_response` can read it
        without triggering a lazy-load outside the session context.
        """
        query = (
            select(ProductBundleModel)
            .options(selectinload(ProductBundleModel.bundled_product))
            .where(
                and_(
                    ProductBundleModel.store_id == store_id,
                    ProductBundleModel.primary_product_id == primary_product_id,
                )
            )
        )
        if active_only:
            query = query.where(ProductBundleModel.is_active.is_(True))
        query = query.order_by(ProductBundleModel.position.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_store(
        self,
        store_id: UUID,
        active_only: bool = False,
    ) -> list[ProductBundleModel]:
        """List all bundles for a store (admin overview)."""
        query = (
            select(ProductBundleModel)
            .options(selectinload(ProductBundleModel.bundled_product))
            .where(ProductBundleModel.store_id == store_id)
        )
        if active_only:
            query = query.where(ProductBundleModel.is_active.is_(True))
        query = query.order_by(
            ProductBundleModel.primary_product_id,
            ProductBundleModel.position.asc(),
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_by_primary_product(
        self, store_id: UUID, primary_product_id: UUID
    ) -> int:
        """Count bundles for a primary product (for limit checks)."""
        from sqlalchemy import func

        query = select(func.count()).where(
            and_(
                ProductBundleModel.store_id == store_id,
                ProductBundleModel.primary_product_id == primary_product_id,
            )
        )
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def exists(
        self,
        store_id: UUID,
        primary_product_id: UUID,
        bundled_product_id: UUID,
    ) -> bool:
        """Check if a specific bundle pair already exists."""
        query = select(ProductBundleModel.id).where(
            and_(
                ProductBundleModel.store_id == store_id,
                ProductBundleModel.primary_product_id == primary_product_id,
                ProductBundleModel.bundled_product_id == bundled_product_id,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    # ── Update ────────────────────────────────────────────────────────────

    async def update(self, bundle: ProductBundleModel) -> ProductBundleModel:
        """Persist changes to an existing bundle (dirty tracking)."""
        await self.session.flush()
        return bundle

    async def reorder(
        self,
        store_id: UUID,
        primary_product_id: UUID,
        ordered_ids: list[UUID],
    ) -> None:
        """Reorder bundles by setting position based on list order.

        Args:
            store_id: Store scope
            primary_product_id: The trigger product
            ordered_ids: Bundle IDs in desired display order
        """
        for position, bundle_id in enumerate(ordered_ids):
            await self.session.execute(
                update(ProductBundleModel)
                .where(
                    and_(
                        ProductBundleModel.id == bundle_id,
                        ProductBundleModel.store_id == store_id,
                        ProductBundleModel.primary_product_id == primary_product_id,
                    )
                )
                .values(position=position)
            )
        await self.session.flush()

    # ── Delete ────────────────────────────────────────────────────────────

    async def delete(self, bundle_id: UUID, store_id: UUID) -> bool:
        """Delete a single bundle by ID (scoped to store)."""
        bundle = await self.get_by_id(bundle_id, store_id=store_id)
        if not bundle:
            return False
        await self.session.delete(bundle)
        await self.session.flush()
        return True

    async def delete_all_for_primary(
        self, store_id: UUID, primary_product_id: UUID
    ) -> int:
        """Delete all bundles for a primary product. Returns count deleted."""
        result = await self.session.execute(
            delete(ProductBundleModel).where(
                and_(
                    ProductBundleModel.store_id == store_id,
                    ProductBundleModel.primary_product_id == primary_product_id,
                )
            )
        )
        await self.session.flush()
        return result.rowcount
