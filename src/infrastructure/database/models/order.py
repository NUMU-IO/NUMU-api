"""Order database model."""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.order import FulfillmentStatus, OrderStatus, PaymentStatus
from src.infrastructure.database import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class OrderModel(Base, UUIDMixin, TimestampMixin):
    """Order database model."""

    __tablename__ = "orders"

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    
    # Status
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus),
        default=OrderStatus.PENDING,
        nullable=False,
        index=True,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus),
        default=PaymentStatus.PENDING,
        nullable=False,
    )
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(
        Enum(FulfillmentStatus),
        default=FulfillmentStatus.UNFULFILLED,
        nullable=False,
    )
    
    # Line items (stored as JSON)
    line_items: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    
    # Addresses (stored as JSON)
    shipping_address: Mapped[dict] = mapped_column(JSONB, nullable=False)
    billing_address: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    
    # Pricing (stored in cents)
    subtotal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shipping_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tax_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    
    # Payment
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    
    # Shipping
    shipping_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    
    # Notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Extra Data
    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)
    
    # Timestamps
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    store = relationship("StoreModel", back_populates="orders", lazy="selectin")
    customer = relationship("CustomerModel", back_populates="orders", lazy="selectin")

    def __repr__(self) -> str:
        return f"<OrderModel(id={self.id}, order_number={self.order_number})>"
