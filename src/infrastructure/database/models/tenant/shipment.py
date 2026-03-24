"""Shipment database model (public schema with tenant_id discriminator)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ShipmentModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Shipment database model tracking carrier deliveries."""

    __tablename__ = "shipments"
    __table_args__ = (
        Index("idx_shipments_store_id_status", "store_id", "status"),
        Index("idx_shipments_store_id_created_at", "store_id", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Carrier info
    carrier: Mapped[str] = mapped_column(String(50), nullable=False, default="bosta")
    carrier_shipment_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    tracking_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    tracking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    awb_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Status (String, not Enum, to allow future carriers without migration)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")

    # Type & linkage
    shipment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="forward"
    )
    parent_shipment_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.shipments.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Shipping details
    shipping_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    shipping_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # COD
    cod_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cod_collected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cod_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Delivery tracking
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Timestamps
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    order = relationship("OrderModel", backref="shipments", lazy="selectin")
    parent_shipment = relationship(
        "ShipmentModel", remote_side="ShipmentModel.id", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<ShipmentModel(id={self.id}, tracking={self.tracking_number}, status={self.status})>"
