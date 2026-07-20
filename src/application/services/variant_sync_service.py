"""Keep a product row and its variant rows telling one story.

The variant row is the sellable unit (cart availability, checkout debits,
PDP prices all read it), while the product row carries the merchant-facing
headline (list pages, main product form). Historically each was written
independently and they drifted. These helpers are the write-through /
roll-up glue every mutation path shares:

- ``ensure_default_variant`` — create the default (empty ``option_values``)
  variant for a product that has none. CSV import and any other path that
  bypasses the routes' materializer must call this, or the product has no
  sellable unit.
- ``sync_simple_product_to_variant`` — push the product's sku / quantity /
  price down to its single default variant after a main-form save. This is
  what makes a simple product's stock edit actually reach the row the cart
  checks.
- ``recompute_product_quantity`` — headline ``products.quantity`` :=
  SUM(variant quantities) after variant-level edits, so the product list
  and the variant matrix agree.

All helpers work on the caller's session/transaction and are safe no-ops
when the product shape doesn't match (e.g. multi-variant products are
never overwritten by the simple-product sync).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.variant import VariantModel

logger = get_logger(__name__)


async def _sku_taken_by_other(
    session: AsyncSession, store_id: UUID, sku: str, exclude_variant_id: UUID | None
) -> bool:
    """True when another variant in the store already carries this SKU
    (uq_variants_store_sku would reject the write)."""
    q = select(VariantModel.id).where(
        VariantModel.store_id == store_id, VariantModel.sku == sku
    )
    if exclude_variant_id is not None:
        q = q.where(VariantModel.id != exclude_variant_id)
    return (await session.execute(q.limit(1))).scalar_one_or_none() is not None


async def ensure_default_variant(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    store_id: UUID,
    product_id: UUID,
    price_cents: int,
    price_currency: str,
    quantity: int,
    sku: str | None,
) -> UUID | None:
    """Create the default variant when the product has none.

    Returns the created variant id, or None when the product already has
    variants (nothing to do). SKU is dropped (with a log) if another
    variant in the store already claims it — the row must exist even if
    the SKU can't ride along.
    """
    existing = (
        await session.execute(
            select(VariantModel.id)
            .where(VariantModel.product_id == product_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    safe_sku = sku or None
    if safe_sku and await _sku_taken_by_other(session, store_id, safe_sku, None):
        logger.warning(
            "default_variant_sku_conflict_dropped",
            product_id=str(product_id),
            sku=safe_sku,
        )
        safe_sku = None

    row = VariantModel(
        tenant_id=tenant_id,
        store_id=store_id,
        product_id=product_id,
        position=0,
        option_values={},
        price_amount=price_cents,
        price_currency=price_currency,
        sku=safe_sku,
        inventory_quantity=max(0, quantity),
    )
    session.add(row)
    await session.flush()
    return row.id


async def sync_simple_product_to_variant(
    session: AsyncSession,
    *,
    product_id: UUID,
) -> dict | None:
    """Write a simple product's headline fields through to its default
    variant. A product qualifies when it has exactly ONE variant and that
    variant has empty ``option_values`` — real option variants own their
    own price/sku/stock and are never overwritten here.

    Returns a dict of the fields that changed, or None when the product
    isn't simple / doesn't exist.
    """
    product = (
        await session.execute(select(ProductModel).where(ProductModel.id == product_id))
    ).scalar_one_or_none()
    if product is None:
        return None

    variants = (
        (
            await session.execute(
                select(VariantModel).where(VariantModel.product_id == product_id)
            )
        )
        .scalars()
        .all()
    )
    if len(variants) != 1 or variants[0].option_values:
        return None
    v = variants[0]

    changed: dict = {}
    if v.inventory_quantity != product.quantity:
        changed["inventory_quantity"] = (v.inventory_quantity, product.quantity)
        v.inventory_quantity = product.quantity
    if v.price_amount != product.price_amount:
        changed["price_amount"] = (v.price_amount, product.price_amount)
        v.price_amount = product.price_amount
        v.price_currency = product.price_currency

    target_sku = product.sku or None
    if target_sku != (v.sku or None):
        # Only move a real SKU down; never blank a variant SKU because the
        # product row has none (the variant may have been set via the
        # variants card or auto-generation).
        if target_sku is not None:
            if await _sku_taken_by_other(session, product.store_id, target_sku, v.id):
                logger.warning(
                    "simple_sync_sku_conflict_skipped",
                    product_id=str(product_id),
                    sku=target_sku,
                )
            else:
                changed["sku"] = (v.sku, target_sku)
                v.sku = target_sku

    if changed:
        await session.flush()
    return changed or None


async def recompute_product_quantity(
    session: AsyncSession, *, product_id: UUID
) -> int | None:
    """Set ``products.quantity`` to the SUM of the product's variant
    quantities. Returns the new total, or None when the product has no
    variants (the manual headline count stays authoritative)."""
    total = (
        await session.execute(
            select(func.sum(VariantModel.inventory_quantity)).where(
                VariantModel.product_id == product_id
            )
        )
    ).scalar_one_or_none()
    if total is None:
        return None
    product = (
        await session.execute(select(ProductModel).where(ProductModel.id == product_id))
    ).scalar_one_or_none()
    if product is None:
        return None
    if product.quantity != total:
        product.quantity = int(total)
        await session.flush()
    return int(total)
