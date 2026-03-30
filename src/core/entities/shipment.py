"""Shipment entity representing a carrier delivery for an order."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class ShipmentStatus(StrEnum):
    """Shipment lifecycle status."""

    PENDING = "pending"
    CREATED = "created"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Terminal statuses that won't change
TERMINAL_STATUSES = {
    ShipmentStatus.DELIVERED,
    ShipmentStatus.RETURNED,
    ShipmentStatus.CANCELLED,
}


class Shipment(BaseEntity):
    """A carrier shipment linked to an order.

    Tracks the full lifecycle of a delivery including COD collection,
    status history, and return shipments.
    """

    store_id: UUID
    tenant_id: UUID
    order_id: UUID

    # Carrier info
    carrier: str = "bosta"
    carrier_shipment_id: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    awb_url: str | None = None

    # Status
    status: ShipmentStatus = ShipmentStatus.PENDING

    # Type & linkage
    shipment_type: str = "forward"  # "forward" or "return"
    parent_shipment_id: UUID | None = None

    # Shipping details
    shipping_method: str | None = None
    shipping_cost: int = 0  # cents

    # COD
    cod_amount: int = 0  # cents, 0 for prepaid
    cod_collected: bool = False
    cod_collected_at: datetime | None = None

    # Delivery tracking
    delivery_attempts: int = 0
    status_history: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Timestamps
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether the shipment is in a final state."""
        return self.status in TERMINAL_STATUSES

    def update_status(self, new_status: ShipmentStatus, description: str = "") -> None:
        """Update status and record in history."""
        self.status_history.append({
            "from": self.status.value
            if isinstance(self.status, ShipmentStatus)
            else self.status,
            "to": new_status.value,
            "description": description,
            "timestamp": datetime.now(UTC).isoformat(),
        })
        self.status = new_status
        self.touch()

    def mark_picked_up(self) -> None:
        """Mark shipment as picked up by carrier."""
        self.update_status(ShipmentStatus.PICKED_UP, "Picked up by carrier")
        self.shipped_at = datetime.now(UTC)

    def mark_delivered(
        self, cod_collected: bool = False, cod_amount: float | None = None
    ) -> None:
        """Mark shipment as delivered."""
        self.update_status(ShipmentStatus.DELIVERED, "Delivered to customer")
        self.delivered_at = datetime.now(UTC)
        if cod_collected:
            self.cod_collected = True
            self.cod_collected_at = datetime.now(UTC)
            if cod_amount is not None:
                self.cod_amount = (
                    int(cod_amount * 100) if cod_amount > 1000 else int(cod_amount)
                )

    def mark_failed(self, reason: str = "") -> None:
        """Record a failed delivery attempt."""
        self.delivery_attempts += 1
        self.update_status(ShipmentStatus.FAILED, f"Delivery failed: {reason}")

    def mark_returned(self) -> None:
        """Mark shipment as returned to sender."""
        self.update_status(ShipmentStatus.RETURNED, "Returned to sender")
        self.cancelled_at = datetime.now(UTC)

    def mark_cancelled(self, reason: str = "") -> None:
        """Mark shipment as cancelled."""
        self.update_status(ShipmentStatus.CANCELLED, f"Cancelled: {reason}")
        self.cancelled_at = datetime.now(UTC)
