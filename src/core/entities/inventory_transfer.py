"""Inventory transfer between locations — Phase 8.2.

A `Transfer` is the audit-trailed move of N units of a variant from
one location to another. The application layer enforces the
invariant "sum across locations is unchanged" — every applied
transfer subtracts from `from_location` and adds to `to_location`
in a single transaction.

State machine:
    DRAFT      → merchant is staging line items
    REQUESTED  → submitted for approval / packing
    IN_TRANSIT → physically moving (the optimistic state we use
                 between when the merchant hits "Ship" and when
                 the receiving location confirms)
    RECEIVED   → terminal: stock moved on the books
    CANCELED   → terminal: never applied (no stock movement)

Stock only moves at the RECEIVED transition. Earlier states are
pure paperwork. This avoids the "stuck-in-transit" problem where a
truck breaks down and stock is invisible to both ends; the merchant
can cancel the transfer and stock stays at the origin.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class TransferStatus(StrEnum):
    DRAFT = "draft"
    REQUESTED = "requested"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    CANCELED = "canceled"


# Valid transitions — same pattern as Order / OrderReturn state machines.
VALID_TRANSFER_TRANSITIONS: dict[TransferStatus, list[TransferStatus]] = {
    TransferStatus.DRAFT: [TransferStatus.REQUESTED, TransferStatus.CANCELED],
    TransferStatus.REQUESTED: [TransferStatus.IN_TRANSIT, TransferStatus.CANCELED],
    TransferStatus.IN_TRANSIT: [TransferStatus.RECEIVED, TransferStatus.CANCELED],
    TransferStatus.RECEIVED: [],  # terminal
    TransferStatus.CANCELED: [],  # terminal
}


class InventoryTransferLine(BaseEntity):
    """One line item on a transfer — N units of a variant."""

    variant_id: UUID
    quantity: int = Field(default=0, ge=1)


class InventoryTransfer(BaseEntity):
    tenant_id: UUID
    store_id: UUID
    from_location_id: UUID
    to_location_id: UUID
    status: TransferStatus = TransferStatus.DRAFT
    # Merchant-supplied label ("June restock from Alex warehouse").
    note: str | None = None
    # Optional carrier reference (waybill number / courier label).
    carrier_reference: str | None = None
    # Lines as embedded JSONB (one transfer averages ~10 lines; a
    # separate table would be over-engineered for the load).
    lines: list[InventoryTransferLine] = Field(default_factory=list)
    # Audit timestamps for each state transition. Set by the
    # repository at transition time. created_at + updated_at on the
    # base entity cover the rest.
    requested_at: datetime | None = None
    shipped_at: datetime | None = None
    received_at: datetime | None = None
    canceled_at: datetime | None = None

    def can_transition_to(self, target: TransferStatus) -> bool:
        return target in VALID_TRANSFER_TRANSITIONS.get(self.status, [])
