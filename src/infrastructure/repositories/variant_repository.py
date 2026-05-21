"""Variant repository — Phase 8.1.

Persistence layer for product variants. Call sites: hub PDP CRUD,
storefront PDP read, cart/checkout variant resolution, search route
variant matching.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.variant import Variant
from src.core.value_objects.money import Money
from src.infrastructure.database.models.tenant.variant import VariantModel


def _to_entity(row: VariantModel) -> Variant:
    return Variant(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        product_id=row.product_id,
        position=row.position,
        option_values=row.option_values or {},
        price=Money(amount=row.price_amount, currency=row.price_currency),
        compare_at_price=(
            Money(amount=row.compare_at_price, currency=row.price_currency)
            if row.compare_at_price is not None
            else None
        ),
        cost_price=(
            Money(amount=row.cost_price, currency=row.price_currency)
            if row.cost_price is not None
            else None
        ),
        sku=row.sku,
        barcode=row.barcode,
        inventory_quantity=row.inventory_quantity,
        image_url=row.image_url,
        weight=float(row.weight) if row.weight is not None else None,
        metadata=row.metadata_ or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _money_amount(m: Money | None) -> int | None:
    if m is None:
        return None
    # Money stores cents as `amount` (int) per the existing codebase
    # convention; if a string-decimal sneaks in we coerce.
    return int(m.amount)


class VariantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, variant_id: UUID) -> Variant | None:
        row = (
            await self._session.execute(
                select(VariantModel).where(VariantModel.id == variant_id)
            )
        ).scalar_one_or_none()
        return _to_entity(row) if row else None

    async def list_for_product(self, product_id: UUID) -> list[Variant]:
        """Return all variants for a product, position-ordered."""
        rows = (
            (
                await self._session.execute(
                    select(VariantModel)
                    .where(VariantModel.product_id == product_id)
                    .order_by(VariantModel.position.asc())
                )
            )
            .scalars()
            .all()
        )
        return [_to_entity(r) for r in rows]

    async def list_for_products(
        self, product_ids: list[UUID]
    ) -> dict[UUID, list[Variant]]:
        """Batch variant fetch — used by PLP / search to avoid N+1."""
        if not product_ids:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(VariantModel)
                    .where(VariantModel.product_id.in_(product_ids))
                    .order_by(
                        VariantModel.product_id.asc(),
                        VariantModel.position.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        out: dict[UUID, list[Variant]] = {pid: [] for pid in product_ids}
        for r in rows:
            out[r.product_id].append(_to_entity(r))
        return out

    async def find_by_options(
        self, product_id: UUID, option_values: dict[str, str]
    ) -> Variant | None:
        """Lookup a specific variant by its option_values.

        Used by the PDP add-to-cart path when the customer picks
        (Size=M, Color=Red) and we need the variant_id to stamp on
        the cart line. JSONB equality with `option_values` works
        because the dict is canonical (axes always present in the
        same key order isn't required — JSONB compares semantically).
        """
        rows = (
            (
                await self._session.execute(
                    select(VariantModel).where(
                        VariantModel.product_id == product_id,
                        VariantModel.option_values == option_values,
                    )
                )
            )
            .scalars()
            .all()
        )
        return _to_entity(rows[0]) if rows else None

    async def create(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        product_id: UUID,
        position: int = 0,
        option_values: dict[str, str] | None = None,
        price: Money,
        compare_at_price: Money | None = None,
        cost_price: Money | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        inventory_quantity: int = 0,
        image_url: str | None = None,
        weight: float | None = None,
        metadata: dict | None = None,
    ) -> Variant:
        row = VariantModel(
            tenant_id=tenant_id,
            store_id=store_id,
            product_id=product_id,
            position=position,
            option_values=option_values or {},
            price_amount=int(price.amount),
            price_currency=price.currency,
            compare_at_price=_money_amount(compare_at_price),
            cost_price=_money_amount(cost_price),
            sku=sku,
            barcode=barcode,
            inventory_quantity=inventory_quantity,
            image_url=image_url,
            weight=weight,
            metadata_=metadata or {},
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_entity(row)

    async def update(self, variant: Variant) -> Variant:
        row = (
            await self._session.execute(
                select(VariantModel).where(VariantModel.id == variant.id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Variant {variant.id} not found")
        row.position = variant.position
        row.option_values = variant.option_values or {}
        row.price_amount = int(variant.price.amount)
        row.price_currency = variant.price.currency
        row.compare_at_price = _money_amount(variant.compare_at_price)
        row.cost_price = _money_amount(variant.cost_price)
        row.sku = variant.sku
        row.barcode = variant.barcode
        row.inventory_quantity = variant.inventory_quantity
        row.image_url = variant.image_url
        row.weight = variant.weight
        row.metadata_ = variant.metadata or {}
        await self._session.flush()
        await self._session.refresh(row)
        return _to_entity(row)

    async def decrement_stock(
        self, variant_id: UUID, qty: int, *, allow_oversell: bool = False
    ) -> int:
        """Atomically deduct `qty` from inventory_quantity.

        Raises `ValueError` when the deduction would push stock
        negative AND `allow_oversell` is False. Returns the new
        inventory_quantity.
        """
        row = (
            await self._session.execute(
                select(VariantModel)
                .where(VariantModel.id == variant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Variant {variant_id} not found")
        new_qty = row.inventory_quantity - qty
        if new_qty < 0 and not allow_oversell:
            raise ValueError(
                f"Cannot deduct {qty} from variant {variant_id}: "
                f"current stock is {row.inventory_quantity}"
            )
        row.inventory_quantity = max(0, new_qty)
        await self._session.flush()
        return row.inventory_quantity

    async def delete_for_product(self, product_id: UUID) -> int:
        """Hard-delete all variants for a product (used when the
        product itself is being deleted; FK cascade handles this
        automatically but we expose the method for the rare case the
        product is being kept while all variants are being recreated)."""
        result = await self._session.execute(
            delete(VariantModel).where(VariantModel.product_id == product_id)
        )
        return result.rowcount or 0

    async def delete_by_id(self, variant_id: UUID) -> bool:
        result = await self._session.execute(
            delete(VariantModel).where(VariantModel.id == variant_id)
        )
        return (result.rowcount or 0) > 0
