"""Shipment API request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateShipmentRequest(BaseModel):
    """Create a shipment for an order."""

    order_id: UUID
    carrier: str = Field(
        default="bosta", description="Shipping carrier: bosta, mylerz, jt"
    )
    shipping_method: str = "standard"
    notes: str | None = None


class BulkCreateShipmentRequest(BaseModel):
    """Bulk create shipments for multiple orders."""

    order_ids: list[UUID] = Field(..., min_length=1, max_length=50)


class ShipmentStatusHistoryEntry(BaseModel):
    """A single entry in shipment status history."""

    from_status: str = Field(alias="from")
    to: str
    description: str = ""
    timestamp: str


class ShipmentResponse(BaseModel):
    """Full shipment detail response."""

    id: UUID
    store_id: UUID
    order_id: UUID
    carrier: str
    carrier_shipment_id: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    awb_url: str | None = None
    status: str
    shipment_type: str
    parent_shipment_id: UUID | None = None
    shipping_method: str | None = None
    shipping_cost: int = 0
    cod_amount: int = 0
    cod_collected: bool = False
    cod_collected_at: datetime | None = None
    delivery_attempts: int = 0
    status_history: list[dict] = []
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ShipmentListItemResponse(BaseModel):
    """Summary shipment for list views."""

    id: UUID
    order_id: UUID
    tracking_number: str | None = None
    carrier: str
    status: str
    shipment_type: str
    shipping_method: str | None = None
    cod_amount: int = 0
    cod_collected: bool = False
    delivery_attempts: int = 0
    created_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None


class ShipmentStatsResponse(BaseModel):
    """Dashboard stats for shipments."""

    total: int = 0
    by_status: dict[str, int] = {}
    cod_total: int = 0
    cod_collected: int = 0
    cod_pending: int = 0


class BulkShipmentResultItem(BaseModel):
    """Result for a single order in bulk creation."""

    order_id: UUID
    success: bool
    tracking_number: str | None = None
    shipment_id: UUID | None = None
    error: str | None = None


class BulkShipmentResultResponse(BaseModel):
    """Bulk shipment creation results."""

    total: int
    succeeded: int
    failed: int
    results: list[BulkShipmentResultItem]


class CodSummaryResponse(BaseModel):
    """COD reconciliation summary."""

    total_shipments: int = 0
    total_expected: int = 0
    total_collected: int = 0
    total_pending: int = 0
    collected_count: int = 0
    delivered_not_collected: int = 0
