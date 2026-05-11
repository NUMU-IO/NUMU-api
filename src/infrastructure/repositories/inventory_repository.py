"""Inventory repositories — Phase 8.2.

Two repos, both thin wrappers over the SQLAlchemy models:

* `InventoryLevelRepository` — (variant × location) stock counts.
  Call sites: hub Inventory dashboard, transfer service (move
  between rows), variant_quantity-rollup on level changes.

* `InventoryTransferRepository` — audit-trailed stock moves.
  State machine enforced at the service layer (TransferService);
  the repo just persists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.inventory_level import InventoryLevel
from src.core.entities.inventory_transfer import (
    InventoryTransfer,
    InventoryTransferLine,
    TransferStatus,
)
from src.infrastructure.database.models.tenant.inventory_level import (
    InventoryLevelModel,
)
from src.infrastructure.database.models.tenant.inventory_transfer import (
    InventoryTransferModel,
)


def _level_to_entity(row: InventoryLevelModel) -> InventoryLevel:
    return InventoryLevel(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        variant_id=row.variant_id,
        location_id=row.location_id,
        available=row.available,
        reserved=row.reserved,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _transfer_to_entity(row: InventoryTransferModel) -> InventoryTransfer:
    lines_raw = row.lines or []
    return InventoryTransfer(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        from_location_id=row.from_location_id,
        to_location_id=row.to_location_id,
        status=TransferStatus(row.status)
        if isinstance(row.status, str)
        else row.status,
        note=row.note,
        carrier_reference=row.carrier_reference,
        lines=[
            InventoryTransferLine(
                variant_id=UUID(line["variant_id"])
                if isinstance(line.get("variant_id"), str)
                else line.get("variant_id"),
                quantity=int(line.get("quantity", 0)),
            )
            for line in lines_raw
            if isinstance(line, dict) and line.get("variant_id")
        ],
        requested_at=row.requested_at,
        shipped_at=row.shipped_at,
        received_at=row.received_at,
        canceled_at=row.canceled_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class InventoryLevelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_variant(self, variant_id: UUID) -> list[InventoryLevel]:
        rows = (
            (
                await self._session.execute(
                    select(InventoryLevelModel).where(
                        InventoryLevelModel.variant_id == variant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_level_to_entity(r) for r in rows]

    async def list_for_location(self, location_id: UUID) -> list[InventoryLevel]:
        rows = (
            (
                await self._session.execute(
                    select(InventoryLevelModel).where(
                        InventoryLevelModel.location_id == location_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_level_to_entity(r) for r in rows]

    async def list_for_store(self, store_id: UUID) -> list[InventoryLevel]:
        rows = (
            (
                await self._session.execute(
                    select(InventoryLevelModel).where(
                        InventoryLevelModel.store_id == store_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_level_to_entity(r) for r in rows]

    async def get(self, variant_id: UUID, location_id: UUID) -> InventoryLevel | None:
        row = (
            await self._session.execute(
                select(InventoryLevelModel)
                .where(
                    InventoryLevelModel.variant_id == variant_id,
                    InventoryLevelModel.location_id == location_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        return _level_to_entity(row) if row else None

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        variant_id: UUID,
        location_id: UUID,
        available: int,
    ) -> InventoryLevel:
        """Set the level to a specific count (idempotent).

        Used by the hub Inventory page's inline edit + the migration's
        manual restock flow. On conflict we replace `available`
        wholesale; for atomic +/- deltas use `adjust()`.
        """
        stmt = (
            pg_insert(InventoryLevelModel)
            .values(
                tenant_id=tenant_id,
                store_id=store_id,
                variant_id=variant_id,
                location_id=location_id,
                available=available,
                reserved=0,
            )
            .on_conflict_do_update(
                constraint="uq_inventory_variant_location",
                set_={"available": available},
            )
            .returning(InventoryLevelModel)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        await self._session.flush()
        return _level_to_entity(row)

    async def adjust(
        self,
        *,
        variant_id: UUID,
        location_id: UUID,
        delta: int,
        allow_negative: bool = False,
    ) -> InventoryLevel:
        """Atomic +/- on `available`. Used by transfer + cart-decrement.

        Raises ValueError when delta would push below zero and
        allow_negative is False.
        """
        row = (
            await self._session.execute(
                select(InventoryLevelModel)
                .where(
                    InventoryLevelModel.variant_id == variant_id,
                    InventoryLevelModel.location_id == location_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(
                f"No inventory_level for variant {variant_id} @ location {location_id}"
            )
        new = row.available + delta
        if new < 0 and not allow_negative:
            raise ValueError(
                f"Insufficient stock at this location: have {row.available}, "
                f"requested delta {delta}"
            )
        row.available = max(0, new)
        await self._session.flush()
        return _level_to_entity(row)


class InventoryTransferRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, transfer_id: UUID) -> InventoryTransfer | None:
        row = (
            await self._session.execute(
                select(InventoryTransferModel).where(
                    InventoryTransferModel.id == transfer_id
                )
            )
        ).scalar_one_or_none()
        return _transfer_to_entity(row) if row else None

    async def list_for_store(
        self, store_id: UUID, status: TransferStatus | None = None
    ) -> list[InventoryTransfer]:
        stmt = select(InventoryTransferModel).where(
            InventoryTransferModel.store_id == store_id
        )
        if status is not None:
            stmt = stmt.where(InventoryTransferModel.status == status)
        stmt = stmt.order_by(InventoryTransferModel.created_at.desc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_transfer_to_entity(r) for r in rows]

    async def create(self, transfer: InventoryTransfer) -> InventoryTransfer:
        row = InventoryTransferModel(
            tenant_id=transfer.tenant_id,
            store_id=transfer.store_id,
            from_location_id=transfer.from_location_id,
            to_location_id=transfer.to_location_id,
            status=transfer.status,
            note=transfer.note,
            carrier_reference=transfer.carrier_reference,
            lines=[
                {"variant_id": str(line.variant_id), "quantity": line.quantity}
                for line in (transfer.lines or [])
            ],
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _transfer_to_entity(row)

    async def transition(
        self, transfer_id: UUID, target: TransferStatus
    ) -> InventoryTransfer:
        """Move a transfer to a new state + stamp the timestamp.

        Stock movement on RECEIVED is handled by the service layer
        (TransferService); the repo only persists state + timestamps.
        """
        row = (
            await self._session.execute(
                select(InventoryTransferModel)
                .where(InventoryTransferModel.id == transfer_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Transfer {transfer_id} not found")
        # Application layer should already have validated the
        # transition via VALID_TRANSFER_TRANSITIONS — we just stamp.
        row.status = target
        now = datetime.now(UTC)
        if target == TransferStatus.REQUESTED:
            row.requested_at = now
        elif target == TransferStatus.IN_TRANSIT:
            row.shipped_at = now
        elif target == TransferStatus.RECEIVED:
            row.received_at = now
        elif target == TransferStatus.CANCELED:
            row.canceled_at = now
        await self._session.flush()
        return _transfer_to_entity(row)

    async def update_lines(
        self, transfer_id: UUID, lines: list[InventoryTransferLine]
    ) -> InventoryTransfer:
        """Replace the lines list. Only valid in DRAFT — service layer
        rejects edits on non-DRAFT transfers."""
        row = (
            await self._session.execute(
                select(InventoryTransferModel).where(
                    InventoryTransferModel.id == transfer_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Transfer {transfer_id} not found")
        row.lines = [
            {"variant_id": str(line.variant_id), "quantity": line.quantity}
            for line in lines
        ]
        await self._session.flush()
        return _transfer_to_entity(row)
