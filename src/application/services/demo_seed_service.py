"""Demo-seed service (Phase 5.11).

Seeds a fresh store with a small starter catalog (5 demo products + 1
collection) so a merchant can preview the storefront before they've
uploaded their own catalog. Opt-in — the storefront onboarding nudges
already drive the merchant to add their first real product; the seed
gives them something to look at while they figure out what to sell.

What gets seeded:
    1 collection: "Starter Collection" (English) / "مجموعة تجريبية" (Arabic)
    5 products:
      - "Classic T-Shirt" / "تي شيرت كلاسيكي"             50.00 EGP
      - "Canvas Tote Bag" / "حقيبة قماشية"                75.00 EGP
      - "Ceramic Mug" / "كوب سيراميك"                     40.00 EGP
      - "Vinyl Sticker Pack" / "حزمة ملصقات فينيل"        25.00 EGP
      - "Hooded Sweatshirt" / "هودي بقلنسوة"             120.00 EGP

All seeded products have:
  - status=ACTIVE (visible in the storefront immediately)
  - quantity=10 (in stock)
  - tag "demo" (so merchants can bulk-delete easily)
  - a placeholder image URL pointing at a stock CDN

The merchant can edit/delete any of these via the normal Products
list. Bulk delete via:
    DELETE /stores/{id}/products?tag=demo

Why seed at all (vs onboarding nudges only):
    The "preview my storefront" loop on day 1 is a gut-check. A
    merchant who lands on an empty home page concludes the platform
    is broken; one with placeholder products understands what they're
    looking at and how it'll feel once they replace the placeholders.
    Shopify, BigCommerce, Wix all do this.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID, uuid4

from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.value_objects.money import Currency, Money

logger = logging.getLogger(__name__)


# Stock product images. CDN-hosted; the merchant replaces them with
# their own as they edit each product. We use a single Cloudflare
# Images base because it's where merchant uploads land too — keeps
# the storefront image-host allowlist (Phase 4.2) covering both.
_STOCK_IMAGE_BASE = "https://imagedelivery.net/numu-demo-seed"

# (en_name, ar_name, en_desc, ar_desc, price_cents, image_slug)
_DEMO_PRODUCTS: list[tuple[str, str, str, str, int, str]] = [
    (
        "Classic T-Shirt",
        "تي شيرت كلاسيكي",
        "Soft cotton tee in three colors. Replace with your own product.",
        "تي شيرت قطني ناعم بثلاثة ألوان. استبدله بمنتجك.",
        5000,
        "tshirt",
    ),
    (
        "Canvas Tote Bag",
        "حقيبة قماشية",
        "Sturdy canvas tote, perfect for everyday use.",
        "حقيبة قماشية متينة مناسبة للاستخدام اليومي.",
        7500,
        "tote",
    ),
    (
        "Ceramic Mug",
        "كوب سيراميك",
        "Hand-glazed ceramic mug, dishwasher-safe.",
        "كوب سيراميك مطلي يدويًا، آمن في غسالة الصحون.",
        4000,
        "mug",
    ),
    (
        "Vinyl Sticker Pack",
        "حزمة ملصقات فينيل",
        "Pack of 10 weatherproof vinyl stickers.",
        "حزمة من 10 ملصقات فينيل مقاومة للماء.",
        2500,
        "stickers",
    ),
    (
        "Hooded Sweatshirt",
        "هودي بقلنسوة",
        "Heavyweight hoodie in three sizes.",
        "هودي ثقيل بثلاثة مقاسات.",
        12000,
        "hoodie",
    ),
]


async def seed_demo_catalog(store_id: UUID, tenant_id: UUID) -> dict:
    """Insert the demo catalog into the store.

    Returns counts + any per-product errors so the caller can show them
    in the response body. The historical version returned only counts
    and logged failures at INFO level, which meant errors were silently
    swallowed in prod (default log threshold is WARNING in many setups)
    and the route returned 200 with `products=0` looking like a no-op.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.category_repository import (
        CategoryRepository,
    )
    from src.infrastructure.repositories.product_repository import ProductRepository

    products_created = 0
    collections_created = 0
    errors: list[dict[str, str]] = []

    async with AsyncSessionLocal() as session:
        cat_repo = CategoryRepository(session)
        prod_repo = ProductRepository(session)

        # 1. Collection. We mint with a stable slug so a merchant who
        #    re-runs seed (e.g. from a Reset Demo button) updates the
        #    same row instead of duplicating.
        from src.core.entities.category import Category

        collection = Category(
            id=uuid4(),
            store_id=store_id,
            tenant_id=tenant_id,
            name="Starter Collection",
            slug="starter-collection",
            description=(
                "Sample products to help you preview your storefront. "
                "Replace with your own catalog."
            ),
            is_active=True,
            position=0,
            extra_data={"demo_seed": True},
        )
        # Nested transaction (SAVEPOINT) so a collection insert failure
        # — typically a slug collision when reseeding — doesn't poison
        # the outer session and abort the subsequent product inserts.
        try:
            async with session.begin_nested():
                await cat_repo.create(collection)
            collections_created = 1
        except Exception as exc:
            logger.warning(
                "demo_seed_collection_skipped: %s: %s",
                type(exc).__name__,
                exc,
            )
            # Re-fetch the existing collection so products can attach to it.
            # Without this, products would FK-reference a never-persisted
            # category id and trip a FK constraint violation themselves.
            from sqlalchemy import select

            from src.infrastructure.database.models.tenant.category import (
                CategoryModel,
            )

            res = await session.execute(
                select(CategoryModel).where(
                    CategoryModel.store_id == store_id,
                    CategoryModel.slug == "starter-collection",
                )
            )
            existing = res.scalar_one_or_none()
            if existing is not None:
                collection.id = existing.id

        # 2. Products. SAVEPOINT per row so one failure (FK violation,
        #    unique-slug clash, RLS denial) doesn't poison the outer txn
        #    and silently kill every subsequent insert.
        for idx, (
            name_en,
            name_ar,
            desc_en,
            desc_ar,
            price_cents,
            slug,
        ) in enumerate(_DEMO_PRODUCTS):
            product = Product(
                id=uuid4(),
                store_id=store_id,
                tenant_id=tenant_id,
                name=name_en,
                slug=f"demo-{slug}",
                sku=f"DEMO-{idx + 1:03d}",
                description=desc_en,
                short_description=desc_en[:120],
                product_type=ProductType.PHYSICAL,
                status=ProductStatus.ACTIVE,
                price=Money(amount=Decimal(price_cents) / 100, currency=Currency.EGP),
                quantity=10,
                images=[f"{_STOCK_IMAGE_BASE}/{slug}/public"],
                tags=["demo"],
                category_id=collection.id,
                attributes={
                    # Phase 3.6 — translated fields live on attributes.
                    # The SDK's useFieldTranslation pulls these when
                    # the active locale is `ar`.
                    "name_ar": name_ar,
                    "description_ar": desc_ar,
                    "demo_seed": True,
                },
            )
            try:
                async with session.begin_nested():
                    await prod_repo.create(product)
                products_created += 1
            except Exception as exc:
                # logger.exception captures the traceback at ERROR level
                # — what we should have been doing all along. Combined
                # with the per-product SAVEPOINT, this means a problem
                # on row 1 no longer cascades through rows 2-5.
                logger.exception(
                    "demo_seed_product_failed: %s (slug=%s)", name_en, f"demo-{slug}",
                )
                errors.append({
                    "name": name_en,
                    "slug": f"demo-{slug}",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                })

        # AsyncSessionLocal doesn't autocommit; exiting the `with` block
        # rolls back uncommitted changes. Wrap the commit in try/except
        # so a failure here (rare, but possible if RLS denies the
        # whole txn at commit-time) surfaces in the response instead of
        # being hidden by the implicit rollback.
        try:
            await session.commit()
        except Exception as exc:
            logger.exception("demo_seed_commit_failed")
            errors.append({
                "name": "__commit__",
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            })
            # Best-effort rollback so the session closes cleanly.
            await session.rollback()
            # Wipe the per-row "created" counters since the commit failed.
            products_created = 0
            collections_created = 0

    logger.info(
        "demo_catalog_seeded",
        extra={
            "store_id": str(store_id),
            "collections": collections_created,
            "products": products_created,
            "errors": len(errors),
        },
    )
    return {
        "products": products_created,
        "collections": collections_created,
        "errors": errors,
    }


async def remove_demo_catalog(store_id: UUID) -> int:
    """Bulk delete every demo-tagged product from the store.

    Used by the hub's "Reset demo" button — merchants who started
    seeded but want a clean slate before launch run this.
    Returns the number of products deleted.
    """
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.product import ProductModel

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ProductModel).where(
                ProductModel.store_id == store_id,
                ProductModel.tags.contains(["demo"]),
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            await session.delete(row)
        await session.commit()
    logger.info("demo_catalog_removed", extra={"store_id": str(store_id), "count": len(rows)})
    return len(rows)
