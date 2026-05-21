"""Inventory orchestration — Phase 8.2.

Two operations the route layer needs but that span multiple repos:

1. `update_level()` — set a single level + roll up the variant's
   total `inventory_quantity` to the sum across locations. Keeps the
   hot-path cart/checkout reads on `variant.inventory_quantity`
   accurate without forcing them to JOIN inventory_levels every read.

2. `apply_transfer_transition()` — drive a transfer through its
   state machine and, on the RECEIVED transition, move stock between
   locations atomically (subtract from each line's from_location,
   add to the to_location, in a single transaction).

Both operations validate at the service layer; the repos are
mechanical persistence only.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.inventory_transfer import (
    VALID_TRANSFER_TRANSITIONS,
    InventoryTransfer,
    TransferStatus,
)
from src.infrastructure.database.models.tenant.variant import VariantModel
from src.infrastructure.repositories.inventory_repository import (
    InventoryLevelRepository,
    InventoryTransferRepository,
)


class InventoryService:
    """Spans level + transfer + variant rollup."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._levels = InventoryLevelRepository(session)
        self._transfers = InventoryTransferRepository(session)

    # ── Levels ────────────────────────────────────────────────────

    async def set_level(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        variant_id: UUID,
        location_id: UUID,
        available: int,
    ) -> int:
        """Set the level at one location, then refresh the variant's
        total inventory_quantity. Returns the new variant total."""
        await self._levels.upsert(
            tenant_id=tenant_id,
            store_id=store_id,
            variant_id=variant_id,
            location_id=location_id,
            available=available,
        )
        return await self._rollup_variant_total(variant_id)

    async def adjust_level(
        self,
        *,
        variant_id: UUID,
        location_id: UUID,
        delta: int,
    ) -> int:
        """Atomic +/- at one location, then refresh the variant total."""
        await self._levels.adjust(
            variant_id=variant_id, location_id=location_id, delta=delta
        )
        return await self._rollup_variant_total(variant_id)

    async def _rollup_variant_total(self, variant_id: UUID) -> int:
        """Recompute variant.inventory_quantity as SUM(available) across
        the variant's levels. Service-layer invariant: every level
        mutation triggers a rollup so the cart's price-snapshot path
        keeps reading the variant's column without joining."""
        levels = await self._levels.list_for_variant(variant_id)
        total = sum(level.available for level in levels)
        row = (
            await self._session.execute(
                select(VariantModel).where(VariantModel.id == variant_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            row.inventory_quantity = total
            await self._session.flush()
        return total

    # ── Transfers ────────────────────────────────────────────────

    async def transition_transfer(
        self, transfer_id: UUID, target: TransferStatus
    ) -> InventoryTransfer:
        """Move a transfer through its state machine. On RECEIVED, move
        stock between locations atomically.

        Raises ValueError on illegal transitions or insufficient
        from-location stock at RECEIVED time.
        """
        current = await self._transfers.get_by_id(transfer_id)
        if current is None:
            raise ValueError(f"Transfer {transfer_id} not found")
        if not current.can_transition_to(target):
            raise ValueError(
                f"Transfer {transfer_id} cannot transition "
                f"{current.status.value} → {target.value}. "
                f"Valid: {[s.value for s in VALID_TRANSFER_TRANSITIONS.get(current.status, [])]}"
            )

        if target == TransferStatus.RECEIVED:
            # Move stock — subtract from from_location, add to
            # to_location, for every line. All in this session's
            # transaction; the route commit at the end persists the
            # whole atomic move.
            for line in current.lines:
                await self._levels.adjust(
                    variant_id=line.variant_id,
                    location_id=current.from_location_id,
                    delta=-line.quantity,
                )
                # Upsert at the destination — the destination row may
                # not exist yet for variants that have never lived
                # there. `set_level` would clobber an existing count,
                # so we read+add manually.
                existing = await self._levels.get(
                    variant_id=line.variant_id,
                    location_id=current.to_location_id,
                )
                if existing is None:
                    await self._levels.upsert(
                        tenant_id=current.tenant_id,
                        store_id=current.store_id,
                        variant_id=line.variant_id,
                        location_id=current.to_location_id,
                        available=line.quantity,
                    )
                else:
                    await self._levels.adjust(
                        variant_id=line.variant_id,
                        location_id=current.to_location_id,
                        delta=line.quantity,
                    )
                # Variant total is invariant across the transfer (we
                # subtract N and add N) — no rollup needed.

        return await self._transfers.transition(transfer_id, target)
