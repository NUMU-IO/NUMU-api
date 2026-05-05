"""Order database model (public schema with tenant_id discriminator)."""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.order import FulfillmentStatus, OrderStatus, PaymentStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class OrderModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Order database model with tenant_id discriminator."""

    __tablename__ = "orders"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Status
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="orderstatus", schema="public"),
        default=OrderStatus.PENDING,
        nullable=False,
        index=True,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="paymentstatus", schema="public"),
        default=PaymentStatus.PENDING,
        nullable=False,
    )
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(
        Enum(FulfillmentStatus, name="fulfillmentstatus", schema="public"),
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

    # Coupon
    coupon_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    coupon_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.coupons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Payment
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )

    # Shipping
    shipping_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Snapshot FKs recording which zone/rate were resolved at checkout.
    # Nullable so pre-shipping-config orders and legacy flows keep working.
    # SET NULL on delete: history should survive config cleanup.
    shipping_zone_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.shipping_zones.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    shipping_rate_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.shipping_rates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # ── Deposit-to-confirm COD snapshot fields ────────────────────
    # All nullable — populated only on the deposit-flow path. See
    # Order entity for semantics.
    deposit_required_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deposit_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deposit_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deposit_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    deposit_gateway: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deposit_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extra Data
    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # UTM attribution tracking
    utm_source: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )
    utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )

    # Funnel deduplication: stable per-visitor ID set by the storefront.
    # Persisted on the order so payment webhooks (paymob/kashier) can read
    # it back when emitting the `order_completed` funnel event.
    session_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Optimistic locking — auto-incremented by SQLAlchemy on every UPDATE
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    __mapper_args__ = {"version_id_col": version}

    # Timestamps
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    store = relationship("StoreModel", back_populates="orders", lazy="selectin")
    customer = relationship("CustomerModel", back_populates="orders", lazy="selectin")
    invoice = relationship(
        "InvoiceModel", back_populates="order", uselist=False, lazy="selectin"
    )
    coupon = relationship("CouponModel", lazy="selectin")
    # InstaPay: zero-or-one intent per order, zero-or-many proofs
    # (re-upload after reject). ``lazy="raise"`` keeps us from
    # accidentally triggering synchronous loads in async request paths
    # — call sites that want them must ``selectinload`` explicitly.
    instapay_intent = relationship(
        "InstapayIntentModel",
        uselist=False,
        lazy="raise",
        viewonly=True,
        primaryjoin="OrderModel.id == InstapayIntentModel.order_id",
        foreign_keys="InstapayIntentModel.order_id",
    )
    payment_proofs = relationship(
        "PaymentProofModel",
        back_populates="order",
        lazy="raise",
        order_by="PaymentProofModel.created_at",
    )

    def __repr__(self) -> str:
        return f"<OrderModel(id={self.id}, order_number={self.order_number})>"
