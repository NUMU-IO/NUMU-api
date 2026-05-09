"""Smart-collection membership sweep (Phase 4.4).

Walks every category whose `extra_data.smart_rules` is set, evaluates
the rules against the active product catalog, and updates each
product's `category_id` to point at the matching smart collection.

Limitations + tradeoffs:
  - Each product belongs to ONE category. Smart collections that
    overlap (a product matches two rule sets) get assigned to whichever
    runs last in the sweep — caveat in the merchant docs. v2 will
    introduce a many-to-many product↔collection table; until then,
    smart collections behave like Shopify's "automatic" collections
    with the same one-collection-per-product limitation.
  - Sweep is hourly by default (matches Shopify's documented cadence).
    A merchant who flips a rule and adds a tag won't see the change
    instantly — they wait at most an hour.

Why we don't run the resolver on each product write:
  Triggering the resolver inline on product saves would inflate write
  latency proportionally to the number of smart collections in the
  store. Hourly batch wins on amortized cost; merchants who need
  faster invalidation can hit the manual /recalculate endpoint
  (added by the hub UI in a follow-up).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


# Hard cap per sweep so a runaway store catalog doesn't lock the
# worker for hours. Excess rolls into the next hour.
MAX_PRODUCTS_PER_SWEEP = 50_000


@celery_app.task(
    name="tasks.smart_collection_sweep",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
)
def smart_collection_sweep_task(self):
    """Recompute smart-collection membership across all stores."""
    try:
        result = run_async(_sweep())
        logger.info("smart-collection sweep complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("smart-collection sweep failed")
        raise self.retry(exc=exc)


async def _sweep() -> dict[str, int]:
    from sqlalchemy import select

    from src.application.services.smart_collection_resolver import (
        SmartCollectionRules,
        matches,
    )
    from src.core.entities.product import ProductStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.category import CategoryModel
    from src.infrastructure.database.models.tenant.product import ProductModel

    categories_processed = 0
    products_assigned = 0

    async with AsyncSessionLocal() as session:
        # Pull every category with a non-empty smart_rules blob. We
        # filter in Python because JSONB existence operators (`?`,
        # `@>`) require the path to be exactly known; the
        # `extra_data.smart_rules` shape may evolve.
        cat_rows = (
            (
                await session.execute(
                    select(CategoryModel).where(CategoryModel.extra_data.isnot(None))
                )
            )
            .scalars()
            .all()
        )

        smart_categories: list[tuple[CategoryModel, SmartCollectionRules]] = []
        for cat in cat_rows:
            extra = cat.extra_data or {}
            rules = SmartCollectionRules.from_dict(extra.get("smart_rules"))
            if rules is None:
                continue
            smart_categories.append((cat, rules))

        if not smart_categories:
            return {
                "categories_processed": 0,
                "products_assigned": 0,
            }

        # Group smart collections by store so we don't load the full
        # catalog more than once per store.
        by_store: dict[Any, list[tuple[CategoryModel, SmartCollectionRules]]] = {}
        for cat, rules in smart_categories:
            by_store.setdefault(cat.store_id, []).append((cat, rules))

        for store_id, cat_pairs in by_store.items():
            # Pull active products for this store. Cap to keep the
            # sweep bounded; pathological catalogs roll into the
            # next pass.
            products = (
                (
                    await session.execute(
                        select(ProductModel)
                        .where(
                            ProductModel.store_id == store_id,
                            ProductModel.status == ProductStatus.ACTIVE,
                        )
                        .limit(MAX_PRODUCTS_PER_SWEEP)
                    )
                )
                .scalars()
                .all()
            )

            for product in products:
                # Pick the LAST matching smart collection (overlap
                # resolution caveat — see module docstring).
                matched_cat: CategoryModel | None = None
                for cat, rules in cat_pairs:
                    if matches(rules, product):
                        matched_cat = cat
                if matched_cat is None:
                    continue
                if product.category_id == matched_cat.id:
                    continue
                product.category_id = matched_cat.id
                products_assigned += 1

            categories_processed += len(cat_pairs)

        await session.commit()

    return {
        "categories_processed": categories_processed,
        "products_assigned": products_assigned,
    }
