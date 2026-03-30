"""Shipment DTOs."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.shipment import Shipment


@dataclass
class ShipmentDTO(BaseDTO):
    """Full shipment data transfer object."""

    id: UUID
    store_id: UUID
    tenant_id: UUID
    order_id: UUID
    carrier: str
    carrier_shipment_id: str | None
    tracking_number: str | None
    tracking_url: str | None
    awb_url: str | None
    status: str
    shipment_type: str
    parent_shipment_id: UUID | None
    shipping_method: str | None
    shipping_cost: int
    cod_amount: int
    cod_collected: bool
    cod_collected_at: datetime | None
    delivery_attempts: int
    status_history: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_entity(cls, entity: Shipment) -> "ShipmentDTO":
        return cls(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            order_id=entity.order_id,
            carrier=entity.carrier,
            carrier_shipment_id=entity.carrier_shipment_id,
            tracking_number=entity.tracking_number,
            tracking_url=entity.tracking_url,
            awb_url=entity.awb_url,
            status=entity.status.value
            if hasattr(entity.status, "value")
            else entity.status,
            shipment_type=entity.shipment_type,
            parent_shipment_id=entity.parent_shipment_id,
            shipping_method=entity.shipping_method,
            shipping_cost=entity.shipping_cost,
            cod_amount=entity.cod_amount,
            cod_collected=entity.cod_collected,
            cod_collected_at=entity.cod_collected_at,
            delivery_attempts=entity.delivery_attempts,
            status_history=entity.status_history,
            metadata=entity.metadata,
            shipped_at=entity.shipped_at,
            delivered_at=entity.delivered_at,
            cancelled_at=entity.cancelled_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CreateShipmentDTO(BaseDTO):
    """DTO for creating a shipment."""

    order_id: UUID
    carrier: str = "bosta"
    shipping_method: str = "standard"
    notes: str | None = None
