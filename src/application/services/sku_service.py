"""SKU policy — one rule for every write path.

A product's SKU identifies its sellable unit everywhere (labels, feeds,
merchant spreadsheets, imports). Policy:

- Merchant provides a SKU → validated for uniqueness within the store
  (against both product rows and variant rows); duplicates are rejected
  with a bilingual 409, never a raw IntegrityError 500.
- Merchant leaves it blank → the platform generates one at CREATE time:
  ``SKU-XXXXXXXX`` (8 chars, uppercase Crockford-style base32 from a
  UUID — unambiguous: no I/L/O/U). Generated once, stable forever —
  never regenerated on edit.

Used by: product create route, CSV import, (MCP create_product rides the
create route). Per-variant auto-generation for the opt-in matrix lands
with the merged editor (Wave C).
"""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.variant import VariantModel

logger = get_logger(__name__)

# Crockford base32 — no I, L, O, U (avoids 1/l, 0/O misreads on labels).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _short_code(n: int = 8) -> str:
    value = uuid.uuid4().int
    chars = []
    for _ in range(n):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(chars)


async def sku_in_use(
    session: AsyncSession,
    store_id: UUID,
    sku: str,
    *,
    exclude_product_id: UUID | None = None,
) -> bool:
    """True when any product or variant in the store already carries the
    SKU. ``exclude_product_id`` exempts the product being edited (its own
    row and its own variants)."""
    pq = select(ProductModel.id).where(
        ProductModel.store_id == store_id, ProductModel.sku == sku
    )
    if exclude_product_id is not None:
        pq = pq.where(ProductModel.id != exclude_product_id)
    if (await session.execute(pq.limit(1))).scalar_one_or_none() is not None:
        return True

    vq = select(VariantModel.id).where(
        VariantModel.store_id == store_id, VariantModel.sku == sku
    )
    if exclude_product_id is not None:
        vq = vq.where(VariantModel.product_id != exclude_product_id)
    return (await session.execute(vq.limit(1))).scalar_one_or_none() is not None


async def generate_unique_sku(session: AsyncSession, store_id: UUID) -> str:
    """A fresh store-unique SKU. Collision odds per try are ~1/32^8; five
    tries then a longer code as the final fallback."""
    for _ in range(5):
        candidate = f"SKU-{_short_code()}"
        if not await sku_in_use(session, store_id, candidate):
            return candidate
    return f"SKU-{_short_code(12)}"


def duplicate_sku_error_detail(sku: str) -> dict:
    """Bilingual 409 payload for a duplicate SKU (the hub's shared
    resolveApiError renders message/message_ar shapes)."""
    return {
        "code": "sku_taken",
        "message": f"SKU '{sku}' is already used in this store.",
        "message_ar": f"كود التخزين (SKU) '{sku}' مستخدم بالفعل في هذا المتجر.",
    }
