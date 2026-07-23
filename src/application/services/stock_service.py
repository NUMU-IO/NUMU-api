"""Single write path for order-driven stock mutations.

Every storage location that holds a stock count is updated together here,
with the same delta, inside the caller's transaction:

    products.quantity                        (headline count, oversell guard)
    product_variants.inventory_quantity      (cart/PDP availability reads)
    attributes.variant_combinations[].stock  (legacy combo catalog — still
        re-materialized into variant rows by the products-route bridge, so
        it must stay consistent until that bridge is retired)
    inventory_levels                         (per-location mirror, only when
        rows already exist for the variant)

Background: checkout used to debit EITHER products.quantity OR the legacy
combo JSONB and never wrote the variant column the cart's availability
guard reads, and no cancel/return path restored anything. This module
closes both gaps.

Deltas are applied uniformly rather than recomputed absolutely — absolute
reconciliation of historically drifted rows is a separate backfill concern.
The variant column may go negative on allowed oversell; that is deliberate,
so debit and restock stay exactly inverse operations.

Restocks replay the debit manifest checkout stamps on
``order.metadata["stock_debited"]``. Orders without the manifest (placed
before this shipped, imported historical orders, merchant-created drafts)
are never restocked — restoring stock that was never debited would inflate
inventory.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.tenant.inventory_level import (
    InventoryLevelModel,
)
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.variant import VariantModel

logger = get_logger(__name__)

RESTOCK_STATUSES = ("cancelled", "returned")
"""Order statuses whose transition restores stock. Refunds do not
auto-restock: money going back does not mean goods came back."""


def _norm(d: dict | None) -> dict:
    """Lowercase both keys and values — the storefront capitalises axis
    names while the hub stores them lowercase (same rule as checkout)."""
    return {str(k).lower(): str(v).lower() for k, v in (d or {}).items()}


async def _resolve_variant_row(
    session: AsyncSession,
    product_id: UUID,
    variant_id: UUID | None,
    selections: dict | None,
) -> VariantModel | None:
    """Find the variant row a line refers to.

    Order of authority: explicit variant_id → option_values matching the
    selections → the product's default variant (empty option_values).
    Returns None when the product has no matching row at all (e.g. CSV
    imports created no variants).
    """
    if variant_id is not None:
        row = (
            await session.execute(
                select(VariantModel).where(VariantModel.id == variant_id)
            )
        ).scalar_one_or_none()
        if row is not None and row.product_id == product_id:
            return row

    rows = (
        (
            await session.execute(
                select(VariantModel).where(VariantModel.product_id == product_id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    if selections:
        target = _norm(selections)
        for row in rows:
            if _norm(row.option_values) == target:
                return row
        return None

    for row in rows:
        if not row.option_values:
            return row
    # Single-variant product whose only row carries option_values (legacy
    # data): treat it as the sellable unit rather than skipping the write.
    return rows[0] if len(rows) == 1 else None


def _find_combo(attrs: dict, selections: dict | None) -> dict | None:
    combos = attrs.get("variant_combinations")
    if not selections or not isinstance(combos, list):
        return None
    target = _norm(selections)
    for combo in combos:
        if isinstance(combo, dict) and _norm(combo.get("options")) == target:
            return combo
    return None


def _combo_stock(combo: dict) -> int:
    raw = combo.get("stock")
    try:
        return int(raw) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


async def _mirror_into_levels(
    session: AsyncSession, variant_row: VariantModel, delta: int
) -> None:
    """Best-effort per-location mirror so a later hub levels-rollup does
    not silently undo the order's delta. Only touches rows that already
    exist — order paths never seed locations. Debits come off the
    location with the most available stock; floors at zero (the variant
    column, not the level rows, is the oversell ledger)."""
    rows = (
        (
            await session.execute(
                select(InventoryLevelModel).where(
                    InventoryLevelModel.variant_id == variant_row.id
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return
    target = max(rows, key=lambda r: r.available) if delta < 0 else rows[0]
    target.available = max(0, target.available + delta)


async def apply_line_delta(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    product_id: UUID,
    variant_id: UUID | None,
    selections: dict | None,
    delta: int,
    allow_negative: bool = True,
) -> tuple[bool, str | None, dict | None]:
    """Apply one stock delta (negative = debit, positive = restock) to
    every storage location, atomically under a product row lock.

    Returns ``(success, failure_reason, replay_entry)``. Failure reasons
    (only possible on debits with ``allow_negative=False``) mirror the
    previous repository API: ``not_found`` / ``no_matching_variant`` /
    ``combo_disabled`` / ``insufficient_stock``.
    """
    product = (
        await session.execute(
            select(ProductModel)
            .where(ProductModel.id == product_id, ProductModel.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if product is None:
        return False, "not_found", None

    # Deep copy: the row's own dict must stay pristine, or SQLAlchemy's
    # old-vs-new comparison sees identical nested objects and silently
    # skips the JSONB UPDATE on flush.
    attrs = copy.deepcopy(product.attributes or {})
    combo = _find_combo(attrs, selections)

    # ── Guards (debit only) ──
    if delta < 0:
        if selections and combo is None:
            return False, "no_matching_variant", None
        if combo is not None and combo.get("enabled") is False:
            return False, "combo_disabled", None
        if not allow_negative:
            available = _combo_stock(combo) if combo is not None else product.quantity
            if available < -delta:
                return False, "insufficient_stock", None

    # ── Writes ──
    product.quantity = product.quantity + delta

    if combo is not None:
        combo["stock"] = str(_combo_stock(combo) + delta)
        product.attributes = attrs  # reassign so SQLAlchemy sees the JSONB change

    variant_row = await _resolve_variant_row(
        session, product_id, variant_id, selections
    )
    if variant_row is not None:
        variant_row.inventory_quantity = variant_row.inventory_quantity + delta
        await _mirror_into_levels(session, variant_row, delta)

    await session.flush()

    return (
        True,
        None,
        {
            "product_id": str(product_id),
            "variant_id": str(variant_row.id) if variant_row is not None else None,
            "selections": selections or None,
            "quantity": abs(delta),
        },
    )


async def debit_order_line(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    product_id: UUID,
    variant_id: UUID | None,
    selections: dict | None,
    quantity: int,
    allow_negative: bool = False,
) -> tuple[bool, str | None, dict | None]:
    """Checkout-time debit. See ``apply_line_delta`` for semantics."""
    return await apply_line_delta(
        session,
        tenant_id=tenant_id,
        product_id=product_id,
        variant_id=variant_id,
        selections=selections,
        delta=-quantity,
        allow_negative=allow_negative,
    )


async def restock_lines(
    session: AsyncSession, *, tenant_id: UUID, lines: list[dict[str, Any]]
) -> int:
    """Replay a debit manifest in reverse. Returns lines restocked."""
    count = 0
    for line in lines:
        try:
            pid = UUID(str(line["product_id"]))
            vid = UUID(str(line["variant_id"])) if line.get("variant_id") else None
            qty = int(line.get("quantity") or 0)
        except (KeyError, TypeError, ValueError):
            logger.warning("restock_line_malformed", line=line)
            continue
        if qty <= 0:
            continue
        ok, reason, _ = await apply_line_delta(
            session,
            tenant_id=tenant_id,
            product_id=pid,
            variant_id=vid,
            selections=line.get("selections"),
            delta=qty,
        )
        if ok:
            count += 1
        else:
            logger.warning("restock_line_failed", product_id=str(pid), reason=reason)
    return count


async def restock_order(
    session: AsyncSession, order, *, reason: str | None = None
) -> bool:
    """Restore the stock an order debited at checkout, exactly once.

    Mutates ``order.metadata`` (idempotency stamp) but does NOT persist
    the order — call it right before the repository ``update`` that saves
    the status transition, so the stamp rides the same write. Runs the
    line replays inside a savepoint: a failure rolls back the partial
    restock and leaves the status change unaffected.

    Returns True when stock was restored, False when there was nothing to
    do (no debit manifest, or already restocked).
    """
    metadata = dict(order.metadata or {})
    debit = metadata.get("stock_debited") or {}
    lines = debit.get("lines") or []
    if not lines:
        return False
    if metadata.get("stock_restocked_at"):
        return False

    async with session.begin_nested():
        restored = await restock_lines(session, tenant_id=order.tenant_id, lines=lines)

    metadata["stock_restocked_at"] = datetime.now(UTC).isoformat()
    if reason:
        metadata["stock_restock_reason"] = reason
    order.metadata = metadata
    logger.info(
        "order_stock_restocked",
        order_id=str(order.id),
        lines_restored=restored,
        reason=reason,
    )
    return True


async def try_restock_order(
    session: AsyncSession, order, *, reason: str | None = None
) -> bool:
    """Fail-open wrapper for webhook/task call sites: a restock problem
    must never break the status transition being processed. Call before
    the repository update that persists the order, so the idempotency
    stamp rides the same write."""
    try:
        return await restock_order(session, order, reason=reason)
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.warning(
            "order_restock_failed",
            order_id=str(getattr(order, "id", None)),
            reason=reason,
            error=str(exc),
        )
        return False


def build_debit_manifest(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``order.metadata["stock_debited"]`` payload checkout stamps."""
    return {"lines": lines, "at": datetime.now(UTC).isoformat()}
